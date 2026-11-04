from pathlib import Path
import gzip,json,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]

class PublicEntryPoints(unittest.TestCase):
    def run_cli(self,*args):
        return subprocess.run([sys.executable,'-m','opsgauge','--data-root',str(ROOT),*args],capture_output=True,text=True)

    def test_case_listing_uses_paper_levels(self):
        result=self.run_cli('list','--task','AD','--profile','native_raw')
        self.assertEqual(result.returncode,0,result.stderr)
        ids=result.stdout.splitlines()
        self.assertEqual(len(set(ids)),150)
        self.assertIn('AD--F020--L3--native_raw',ids)

    def test_index_and_source_bindings(self):
        with tempfile.TemporaryDirectory() as temp:
            result=self.run_cli('verify','--metadata-only','--output',str(Path(temp)/'check.json'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)['passed'])

    def test_replay_has_no_live_collector_modules(self):
        with gzip.open(ROOT/'data/file-index.jsonl.gz','rt') as stream:
            runtime=[json.loads(line)['path'] for line in stream if '/runtime/bench_factory/' in line]
        self.assertTrue(runtime)
        self.assertFalse(any('/brise/' in path or path.endswith('/cli.py') for path in runtime))

    @unittest.skipUnless((ROOT/'data/objects').exists(),'restore evidence before running scoring regressions')
    def test_saved_submission_rewards(self):
        with tempfile.TemporaryDirectory() as temp:
            result=self.run_cli('check-scoring','--output',str(Path(temp)/'check.json'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)['passed'])

if __name__=='__main__':unittest.main()
