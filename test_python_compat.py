"""Guard all delivered Python source against Python-2-style reload calls."""
import ast
from pathlib import Path
import unittest

def reload_issues(source):
    tree=ast.parse(source)
    modules={n.asname or n.name for n in ast.walk(tree) if isinstance(n,ast.Import) for n in n.names if n.name=='importlib'}
    functions={n.asname or n.name for node in ast.walk(tree) if isinstance(node,ast.ImportFrom) and node.module=='importlib' for n in node.names if n.name=='reload'}
    issues=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):continue
        f=node.func
        if isinstance(f,ast.Name) and f.id=='reload' and f.id not in functions:
            issues.append((node.lineno,'reload must be imported from importlib'))
        if isinstance(f,ast.Attribute) and f.attr=='reload' and isinstance(f.value,ast.Name) and f.value.id=='importlib' and f.value.id not in modules:
            issues.append((node.lineno,'importlib module must be imported'))
    return issues

class PythonCompatibilityTests(unittest.TestCase):
    def test_all_delivered_source_has_no_undefined_reload(self):
        root=Path(__file__).parent
        for path in root.rglob('*.py'):
            if any(part.startswith('.') or part=='__pycache__' for part in path.relative_to(root).parts):continue
            with self.subTest(path=path.name):self.assertEqual(reload_issues(path.read_text(encoding='utf-8')),[])
    def test_guard_detects_removed_builtin(self):
        self.assertTrue(reload_issues('reload(module)'))
    def test_guard_detects_missing_module_import(self):
        self.assertTrue(reload_issues('importlib.reload(module)'))
    def test_correct_python3_module_form(self):
        self.assertEqual(reload_issues('import importlib\nimportlib.reload(module)'),[])
    def test_correct_python3_function_form(self):
        self.assertEqual(reload_issues('from importlib import reload\nreload(module)'),[])
