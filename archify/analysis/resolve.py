"""Conservative lexical resolution. Never match a global symbol by basename."""
import ast
import builtins
from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    status: str
    targets: tuple = ()
    reason: str = ''
    reference: str = ''


UNKNOWN = Result('unresolved', reason='dynamic expression or unknown receiver')


class Resolver:
    def __init__(self, index):
        self.index = index

    def symbol(self, key):
        return Result('resolved', (key,), 'lexical source binding')

    def combine(self, rows):
        unique = list(dict.fromkeys(rows))
        if not unique:
            return UNKNOWN
        if len(unique) == 1:
            return unique[0]
        targets = tuple(sorted({t for r in unique for t in r.targets}))
        return Result('ambiguous', targets, 'multiple bindings or wildcard exports')

    def lookup(self, name, scope, seen=frozenset()):
        token = (scope.key, name)
        if token in seen:
            return Result('unresolved', reason='cyclic import or binding')
        seen = seen | {token}
        bindings = scope.bindings.get(name, [])
        if bindings:
            rows = []
            for kind, value in bindings:
                if kind == 'scope':
                    rows.append(self.symbol(value))
                elif kind in ('import', 'module'):
                    rows.append(self.reference(value, seen, module_only=kind == 'module'))
                else:
                    rows.append(Result('unresolved', reason=value))
            latest = max(scope.binding_lines.get(name, [0]))
            uncertain = 0 in scope.binding_lines.get(name, [0])
            rows += self.star_exports(name, scope, seen, after=0 if uncertain else latest)
            return self.combine(rows)
        rows = self.star_exports(name, scope, seen)
        if rows:
            return self.combine(rows)
        parent = scope.parent
        # Python method/nested function name lookup skips class namespaces.
        while parent and parent.kind == 'class':
            parent = parent.parent
        if parent:
            return self.lookup(name, parent, seen)
        if hasattr(builtins, name):
            return Result('builtin', reference='builtins.' + name)
        return Result('unresolved', reason='name has no known binding')

    def star_exports(self, name, scope, seen, after=0):
        rows = []
        for module, line in scope.stars:
            if line < after:
                continue
            source = self.index.modules.get(module)
            if source is None or source.exports_dynamic:
                rows.append(Result('unresolved', reason='unknown wildcard exports'))
            elif source.exports is not None and name not in source.exports:
                continue
            elif source.exports is None and name.startswith('_'):
                continue
            elif name in source.bindings or source.stars:
                rows.append(self.lookup(name, source, seen))
        return rows

    def reference(self, dotted, seen=frozenset(), module_only=False):
        if dotted in self.index.module_collisions:
            return Result('unresolved', reason='module/file package identity collision')
        parent_name, _, member = dotted.rpartition('.')
        parent = self.index.modules.get(parent_name)
        if not module_only and parent and member in parent.bindings:
            bindings = parent.bindings[member]
            if not (dotted in self.index.modules and bindings == [('import', dotted)]):
                return self.lookup(member, parent, seen)
        if dotted in self.index.modules:
            return self.symbol(self.index.modules[dotted].key)
        parts = dotted.split('.')
        for split in range(len(parts) - 1, 0, -1):
            module = self.index.modules.get('.'.join(parts[:split]))
            if module is None:
                continue
            if module.module in self.index.module_collisions:
                return Result('unresolved', reason='module/file package identity collision')
            result = self.lookup(parts[split], module, seen)
            for attribute in parts[split + 1:]:
                result = self.attribute(result, attribute, seen)
            return result
        internal = any(m == parts[0] or m.startswith(parts[0] + '.') for m in self.index.modules)
        if internal or dotted.startswith('<outside-root>'):
            return Result('unresolved', reason='import target outside scanned modules', reference=dotted)
        return Result('external', reference=dotted, reason='outside repository')

    def mro(self, cls, seen=frozenset()):
        if cls.key in seen:
            return None
        bases = []
        for expression in cls.node.bases:
            if isinstance(expression, ast.Name) and expression.id == 'object':
                continue
            value = self.expression(expression, cls.parent)
            if value.status != 'resolved' or len(value.targets) != 1:
                return None
            base = self.index.scopes[value.targets[0]]
            if base.kind != 'class':
                return None
            bases.append(base)
        sequences = [self.mro(base, seen | {cls.key}) for base in bases]
        if any(seq is None for seq in sequences):
            return None
        sequences = [list(seq) for seq in sequences] + [[b.key for b in bases]]
        result = [cls.key]
        while any(sequences):
            sequences = [seq for seq in sequences if seq]
            candidate = next((seq[0] for seq in sequences
                              if not any(seq[0] in s[1:] for s in sequences)), None)
            if candidate is None:
                return None
            result.append(candidate)
            for sequence in sequences:
                if sequence[0] == candidate:
                    sequence.pop(0)
        return result

    def method(self, cls, name, skip=False):
        order = self.mro(cls)
        if order is None:
            if not skip and name in cls.bindings:
                return self.lookup(name, cls)
            return Result('unresolved', reason='inheritance includes unknown or inconsistent bases')
        for key in order[1 if skip else 0:]:
            scope = self.index.scopes[key]
            if name in scope.bindings:
                return self.lookup(name, scope)
        return Result('unresolved', reason='attribute absent from known class hierarchy')

    def attribute(self, receiver, name, seen=frozenset()):
        if receiver.status == 'external':
            return Result('external', reference=receiver.reference + '.' + name)
        if receiver.status != 'resolved' or len(receiver.targets) != 1:
            return UNKNOWN
        scope = self.index.scopes[receiver.targets[0]]
        if scope.kind == 'module':
            if name in scope.bindings or scope.stars:
                return self.lookup(name, scope, seen)
            child = self.index.modules.get(scope.module + '.' + name)
            return self.symbol(child.key) if child else UNKNOWN
        if scope.kind == 'class':
            return self.method(scope, name)
        return UNKNOWN

    def receiver_class(self, node, scope):
        if scope.kind != 'function' or not scope.parent or scope.parent.kind != 'class':
            return None
        args = scope.node.args.posonlyargs + scope.node.args.args
        static = any(isinstance(d, ast.Name) and d.id == 'staticmethod' for d in scope.node.decorator_list)
        if static or not args or not isinstance(node, ast.Name) or node.id != args[0].arg:
            return None
        if scope.bindings.get(node.id) != [('unknown', 'parameter')]:
            return None
        return scope.parent

    def expression(self, node, scope):
        if isinstance(node, ast.Name):
            return self.lookup(node.id, scope)
        if not isinstance(node, ast.Attribute):
            return UNKNOWN
        cls = self.receiver_class(node.value, scope)
        if cls:
            value = self.method(cls, node.attr)
            return Result('candidate', value.targets, 'virtual dispatch; subclasses may override') if value.targets else value
        if isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == 'super':
                return self.super_method(call, node.attr, scope)
            receiver = self.expression(call.func, scope)
            if receiver.targets and self.index.scopes[receiver.targets[0]].kind == 'class':
                value = self.attribute(receiver, node.attr)
                return Result('candidate', value.targets, 'constructed receiver; __new__/metaclass not evaluated') if value.targets else value
            return UNKNOWN
        return self.attribute(self.expression(node.value, scope), node.attr)

    def super_method(self, call, name, scope):
        if self.lookup('super', scope).status != 'builtin':
            return UNKNOWN
        cls = scope.parent if scope.parent and scope.parent.kind == 'class' else None
        if cls is None:
            return UNKNOWN
        if call.args:
            if len(call.args) != 2 or not self.receiver_class(call.args[1], scope):
                return UNKNOWN
            declared = self.expression(call.args[0], scope)
            if declared.targets != (cls.key,):
                return UNKNOWN
        value = self.method(cls, name, skip=True)
        return Result('candidate', value.targets, 'super dispatch in declared MRO; runtime subclass unknown') if value.targets else value
