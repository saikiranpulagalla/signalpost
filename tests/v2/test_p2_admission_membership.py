"""Execute the runner's actual admission inversion against an independent oracle."""
import ast,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
import norway_company_agent.admission as admission

class Membership(unittest.TestCase):
    def test_runner_builds_each_admitted_set_once_without_semantic_changes(self):
        tree=ast.parse((ROOT/'scripts/run_competition_batch.py').read_text(encoding='utf-8'))
        assignment=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='admitted_by_org' for t in n.targets))
        class Counted(list):
            count=0
            def __iter__(self):
                self.count+=1;return super().__iter__()
        for n,limit in ((100,100),(100,5),(100,0),(100,47)):
            orgs=[str(i) for i in range(n)];pairs={'roles':Counted(orgs[:limit]),'financials':Counted(orgs[-limit:] if limit else [])}
            expected={o:sorted(k for k,values in pairs.items() if o in values) for o in orgs}
            namespace={'orgs':orgs,'admission_by_module':pairs,**vars(admission)}
            exec(compile(ast.Module(body=[assignment],type_ignores=[]),'<production-admission>','exec'),namespace)
            self.assertEqual(namespace['admitted_by_org'],expected)
            self.assertLessEqual(sum(v.count for v in pairs.values()),len(pairs))
