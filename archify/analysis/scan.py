"""Record every syntactic call, including calls that cannot be resolved."""
import ast
from collections import Counter
from .resolve import Resolver, Result


def expression_text(node):
    return ast.unparse(node)[:240]


class Scanner(ast.NodeVisitor):
    def __init__(self, index):
        self.index = index
        self.resolver = Resolver(index)
        self.calls, self.references, self.controls, self.returns = [], [], [], []
        self.scope = None
        self.dynamic_depth = 0
        self.shadowed = set()
        self.guards = []

    def location(self, node):
        return {'path': self.scope.path, 'line': node.lineno, 'column': node.col_offset}

    def run(self):
        for scope in list(self.index.scopes.values()):
            if scope.kind != 'module':
                continue
            self.scope = scope
            self.visit(scope.node)
        return self

    def definition(self, node):
        for decorator in node.decorator_list:
            self.visit(decorator)
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword.value)
        else:
            for default in node.args.defaults + node.args.kw_defaults:
                if default:
                    self.visit(default)
            for argument in ast.iter_child_nodes(node.args):
                if isinstance(argument, ast.arg) and argument.annotation:
                    self.visit(argument.annotation)
            if node.returns:
                self.visit(node.returns)
        previous, guards = self.scope, self.guards
        self.scope, self.guards = self.index.by_node[id(node)], []
        for statement in node.body:
            self.visit(statement)
        self.scope, self.guards = previous, guards

    visit_FunctionDef = definition
    visit_AsyncFunctionDef = definition
    visit_ClassDef = definition

    def visit_Call(self, node):
        names = {n.id for n in ast.walk(node.func) if isinstance(n, ast.Name)}
        blocked = self.dynamic_depth or bool(names & self.shadowed)
        result = Result('unresolved', reason='lambda/comprehension binding requires manual review') \
            if blocked else self.resolver.expression(node.func, self.scope)
        row = {'id': f'{self.scope.path}:{node.lineno}:{node.col_offset}',
               'caller': self.scope.key, 'expression': expression_text(node.func),
               'end_line': node.end_lineno, 'end_column': node.end_col_offset,
               **self.location(node), 'status': result.status,
               'targets': list(result.targets), 'reason': result.reason,
               'reference': result.reference, 'guards': [dict(g) for g in self.guards]}
        # Calling a module is not a resolved function call.
        if result.targets and any(self.index.scopes[t].kind == 'module' for t in result.targets):
            row.update(status='unresolved', reason='module value used as callable', targets=[])
        self.calls.append(row)
        self.record_arguments(node, row['id'])
        self.generic_visit(node)

    def record_arguments(self, node, call_id):
        if self.dynamic_depth or self.shadowed:
            return
        for argument in list(node.args) + [k.value for k in node.keywords]:
            if not isinstance(argument, (ast.Name, ast.Attribute)):
                continue
            value = self.resolver.expression(argument, self.scope)
            targets = [t for t in value.targets if self.index.scopes[t].kind == 'function']
            if targets:
                self.references.append({'call': call_id, **self.location(argument),
                    'expression': expression_text(argument), 'targets': targets,
                    'kind': 'callable_argument', 'status': value.status,
                    'reason': 'passed as value; invocation is not established'})

    def visit_If(self, node):
        self.controls.append({'owner': self.scope.key, 'kind': 'if', **self.location(node)})
        self.visit(node.test)
        condition = expression_text(node.test)
        self.guards.append({'line': node.lineno, 'condition': condition, 'branch': 'then'})
        for statement in node.body:
            self.visit(statement)
        self.guards[-1]['branch'] = 'else'
        for statement in node.orelse:
            self.visit(statement)
        self.guards.pop()

    def visit_Return(self, node):
        self.returns.append({'owner': self.scope.key, **self.location(node),
                             'expression': expression_text(node.value) if node.value else 'None'})
        self.generic_visit(node)

    def visit_For(self, node):
        self.controls.append({'owner': self.scope.key, 'kind': type(node).__name__, **self.location(node)})
        self.generic_visit(node)

    visit_AsyncFor = visit_For
    visit_While = visit_For
    visit_Try = visit_For
    visit_Match = visit_For

    def visit_Lambda(self, node):
        self.dynamic_depth += 1
        self.generic_visit(node)
        self.dynamic_depth -= 1

    def visit_ListComp(self, node):
        previous = self.shadowed
        class_body = self.scope.kind == 'class'
        if class_body:
            self.dynamic_depth += 1
        bound = {name.id for generator in node.generators for name in ast.walk(generator.target)
                 if isinstance(name, ast.Name)}
        self.shadowed = previous | bound
        self.generic_visit(node)
        self.shadowed = previous
        if class_body:
            self.dynamic_depth -= 1

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


def build_report(index):
    scan = Scanner(index).run()
    counts = Counter(row['status'] for row in scan.calls)
    calls_by_owner = Counter(row['caller'] for row in scan.calls)
    branches = Counter(row['owner'] for row in scan.controls)
    targets = {target for row in scan.calls for target in row['targets']}
    symbols = []
    for scope in index.scopes.values():
        symbols.append({'id': scope.key, 'module': scope.module, 'path': scope.path,
            'name': getattr(scope.node, 'name', '<module>'), 'kind': scope.kind,
            'line': getattr(scope.node, 'lineno', 1),
            'runtime_line': min([getattr(scope.node, 'lineno', 1)] +
                                [d.lineno for d in getattr(scope.node, 'decorator_list', [])]),
            'end_line': getattr(scope.node, 'end_lineno', None),
            'parent': scope.parent.key if scope.parent else None,
            'decorated': bool(getattr(scope.node, 'decorator_list', [])),
            'calls': calls_by_owner[scope.key], 'control_sites': branches[scope.key],
            'no_known_incoming_call': scope.key not in targets,
            'behavior_review': 'not_reviewed', 'runtime_test': 'not_run'})
    return {'schema_version': 1, 'language': 'python', 'source_root': str(index.root),
        'mode': 'ast-only-no-target-imports', 'files': index.files,
        'parse_errors': index.errors, 'skipped_files': index.skipped,
        'symbols': symbols, 'calls': scan.calls, 'callable_arguments': scan.references,
        'control_sites': scan.controls, 'returns': scan.returns,
        'coverage': {'files_discovered': len(index.files),
            'files_parsed': sum(f['parsed'] for f in index.files),
            'symbols': len(symbols), 'calls': len(scan.calls),
            'call_statuses': dict(sorted(counts.items())),
            'callable_arguments': len(scan.references)},
        'limits': [
            'Static bindings are source evidence, not proof of execution or runtime identity.',
            'Self/super/constructed receivers are candidates; monkey patches and descriptors are not evaluated.',
            'Assignments, higher-order factories and dynamic registries are unresolved.',
            'Lambda and comprehension-bound callables are inventoried but intentionally unresolved.',
            'Control sites and returns are inventory, not branch coverage or verified output contracts.',
            'Uncalled symbols may be public entry points or callbacks; they are not proven dead code.',
            'Only Python .py files are parsed; binary extensions and other languages are not analyzed.',
        ]}
