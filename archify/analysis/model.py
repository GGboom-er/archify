"""Parse source files and collect lexical bindings without executing them."""
import ast
import hashlib
import os
import stat
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

EXCLUDED = {'.git', '.hg', '.svn', '.venv', 'venv', 'node_modules',
            '__pycache__', '.mypy_cache', '.pytest_cache', '.archify'}


def is_link(path):
    attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    return path.is_symlink() or bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


@dataclass
class Scope:
    key: str
    module: str
    path: str
    node: ast.AST
    kind: str
    parent: object = None
    bindings: dict = field(default_factory=dict)
    stars: list = field(default_factory=list)
    exports: object = None
    exports_dynamic: bool = False
    binding_lines: dict = field(default_factory=dict)

    def bind(self, name, kind, value, line=0):
        self.bindings.setdefault(name, []).append((kind, value))
        self.binding_lines.setdefault(name, []).append(line)


class Index:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.modules, self.scopes, self.by_node = {}, {}, {}
        self.module_collisions = set()
        self.files, self.errors, self.skipped = [], [], []
        self.prefix = self.root.name if (self.root / '__init__.py').is_file() else ''

    def add_scope(self, module, path, node, kind, parent=None):
        name = getattr(node, 'name', '<module>')
        key = f'{path}:{node.lineno}:{name}' if parent else f'{path}:<module>'
        scope = Scope(key, module, path, node, kind, parent)
        self.scopes[key] = scope
        self.by_node[id(node)] = scope
        return scope

    def load(self):
        if not self.root.is_dir():
            raise ValueError('Repository root must be an existing directory')
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and
                             not is_link(Path(directory, d)))
            for filename in sorted(files):
                if filename.endswith('.py'):
                    self.read_file(Path(directory, filename))
        if not self.files:
            raise ValueError('No Python source files found in repository root')
        for scope in list(self.scopes.values()):
            Collector(self, scope).visit(scope.node)
        return self

    def read_file(self, path):
        relative = path.relative_to(self.root).as_posix()
        if is_link(path) or not path.resolve().is_relative_to(self.root):
            self.skipped.append({'path': relative, 'reason': 'symlink'})
            return
        raw = path.read_bytes()
        row = {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()}
        self.files.append(row)
        try:
            with tokenize.open(path) as stream:
                tree = ast.parse(stream.read(), filename=relative)
        except (SyntaxError, UnicodeError, LookupError) as error:
            row['parsed'] = False
            self.errors.append({'path': relative, 'reason': str(error)})
            return
        parts = list(Path(relative).with_suffix('').parts)
        if parts[-1] == '__init__':
            parts.pop()
        module = '.'.join(([self.prefix] if self.prefix else []) + parts)
        row.update(parsed=True, module=module)
        scope = self.add_scope(module, relative, tree, 'module')
        if module in self.modules:
            self.module_collisions.add(module)
            self.errors.append({'path': relative, 'reason': 'duplicate module: ' + module})
        else:
            self.modules[module] = scope


def import_base(scope, node):
    if not node.level:
        return node.module or ''
    package = scope.module.split('.')
    if not scope.path.endswith('__init__.py'):
        package = package[:-1]
    if node.level > len(package):
        return '<outside-root>.' + (node.module or '')
    package = package[:len(package) - node.level + 1]
    return '.'.join(package + ([node.module] if node.module else []))


class Collector(ast.NodeVisitor):
    def __init__(self, index, scope):
        self.index, self.scope = index, scope
        self.conditional = 0

    def bind(self, node, name, kind, value):
        self.scope.bind(name, kind, value, 0 if self.conditional else node.lineno)

    def definition(self, node, kind):
        child = self.index.add_scope(self.scope.module, self.scope.path, node, kind, self.scope)
        self.bind(node, node.name, 'scope', child.key)
        if kind == 'function':
            args = node.args
            names = args.posonlyargs + args.args + args.kwonlyargs
            names += [a for a in (args.vararg, args.kwarg) if a]
            for arg in names:
                child.bind(arg.arg, 'unknown', 'parameter')
        visitor = Collector(self.index, child)
        for statement in node.body:
            visitor.visit(statement)

    def visit_FunctionDef(self, node):
        self.definition(node, 'function')

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.definition(node, 'class')

    def visit_Lambda(self, node):
        # Lambda scope is kept separate by the call scanner.
        pass

    def visit_Import(self, node):
        for alias in node.names:
            name = alias.asname or alias.name.split('.')[0]
            value = alias.name if alias.asname else alias.name.split('.')[0]
            self.bind(node, name, 'module', value)

    def visit_ImportFrom(self, node):
        base = import_base(self.scope, node)
        for alias in node.names:
            if alias.name == '*':
                self.scope.stars.append((base, node.lineno))
            else:
                self.bind(node, alias.asname or alias.name, 'import', base + '.' + alias.name)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.scope.bind(node.id, 'unknown', 'assignment or deletion')

    def visit_Global(self, node):
        for name in node.names:
            self.scope.bind(name, 'unknown', 'global/nonlocal mutation')

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, node):
        if node.name:
            self.scope.bind(node.name, 'unknown', 'exception binding')
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name:
            self.scope.bind(node.name, 'unknown', 'match capture')
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        if node.rest:
            self.scope.bind(node.rest, 'unknown', 'match capture')
        self.generic_visit(node)

    def visit_If(self, node):
        self.conditional += 1
        self.generic_visit(node)
        self.conditional -= 1

    visit_For = visit_If
    visit_AsyncFor = visit_If
    visit_While = visit_If
    visit_Try = visit_If
    visit_With = visit_If
    visit_AsyncWith = visit_If
    visit_Match = visit_If

    def visit_Assign(self, node):
        if any(isinstance(t, ast.Name) and t.id == '__all__' for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
                if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
                    raise ValueError('dynamic exports')
                self.scope.exports = value
            except (ValueError, TypeError):
                self.scope.exports_dynamic = True
        self.generic_visit(node)

    def visit_comprehension(self, node):
        # Comprehension targets do not bind in the containing lexical scope.
        self.visit(node.iter)
        for condition in node.ifs:
            self.visit(condition)
