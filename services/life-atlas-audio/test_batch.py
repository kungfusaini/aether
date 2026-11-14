"""Full-batch guards/resume tests, synthetic data only; no real inference."""
import copy
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import batch
import trial


def manifest():
    return {'batch_id':'full-logs-20990101','files':[
        {'name':name,'size':len(name.encode()),'sha256':hashlib.sha256(name.encode()).hexdigest(),'duration_seconds':1.0}
        for name in batch.NAMES]}


class BatchTests(unittest.TestCase):
    def test_manifest_requires_exact_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'manifest.json'
            p.write_text(json.dumps(manifest()))
            self.assertEqual(batch.load_manifest(p)[0]['files'][0]['name'],'log001.wav')
            for mutation in ['name','hash','duration','bool','batch']:
                data=manifest()
                if mutation=='name':data['files'][0]['name']='other.wav'
                if mutation=='hash':data['files'][0]['sha256']='bad'
                if mutation=='duration':data['files'][0]['duration_seconds']=3601
                if mutation=='bool':data['files'][0]['duration_seconds']=True
                if mutation=='batch':data['batch_id']='../../escape'
                p.write_text(json.dumps(data))
                with self.assertRaises(RuntimeError):batch.load_manifest(p)

    def test_full_deadline_does_not_weaken_pilot(self):
        lock=trial.read_lock()
        args=batch.full_engine_args(lock,Path('/input'),Path('/output'),'synthetic','full-logs-20990101')
        self.assertIn('7200s',args[args.index('-c')+1])
        self.assertIn('nice -n 10',args[args.index('-c')+1])
        self.assertEqual(args[args.index('--pull')+1],'never')
        self.assertEqual(args[args.index('--memory')+1],'1536m')
        self.assertEqual(args[args.index('--network')+1],'none')
        self.assertIn('-ojf',args)
        self.assertIn('900s',trial.deadline_script(900))
        with self.assertRaises(RuntimeError):trial.deadline_script(7200)

    def test_full_decode_has_no_excerpt_cutoff(self):
        args=batch.decode_args(Path('/private/source.wav'),Path('/private/input.wav'))
        self.assertNotIn('-t',args)
        self.assertNotIn('-ss',args)
        self.assertEqual(args[args.index('-i')+1],'/private/source.wav')
        self.assertEqual(args[-1],'/private/input.wav')

    def test_source_integrity_and_symlink_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);item=manifest()['files'][0];p=root/item['name']
            p.write_bytes(item['name'].encode())
            self.assertEqual(batch.verified_source(root,item),p)
            p.write_bytes(b'changed')
            with self.assertRaises(RuntimeError):batch.verified_source(root,item)
            p.unlink();target=root/'synthetic';target.write_bytes(item['name'].encode());p.symlink_to(target)
            with self.assertRaises(RuntimeError):batch.verified_source(root,item)

    def test_missing_sources_rejected_before_inference_intent(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(trial,'STATE',Path(directory)):
            root=Path(directory);m=manifest();stage=root/'staging'/m['batch_id'];stage.mkdir(parents=True,mode=0o700)
            path=stage/'source-manifest.json';path.write_text(json.dumps(m))
            lock=copy.deepcopy(trial.read_lock());models=root/'models';models.mkdir();model=models/'fixture.bin';model.write_bytes(b'synthetic model')
            lock['model'].update(filename=model.name,sha256=trial.sha256(model))
            with patch.object(trial,'read_lock',return_value=lock),patch.object(trial,'engine_guard') as guard:
                with self.assertRaises(RuntimeError):batch.run_batch(path)
                guard.assert_not_called()

    def test_resume_skips_only_verified_completed_outputs(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(trial,'STATE',Path(directory)):
            root=Path(directory);m=manifest();stage=root/'staging'/m['batch_id'];stage.mkdir(parents=True,mode=0o700)
            for item in m['files']:(stage/item['name']).write_bytes(item['name'].encode())
            path=stage/'source-manifest.json';path.write_text(json.dumps(m))
            lock=copy.deepcopy(trial.read_lock());models=root/'models';models.mkdir();model=models/'fixture.bin';model.write_bytes(b'synthetic model')
            lock['model'].update(filename=model.name,sha256=trial.sha256(model))
            def fake(item,source,stage,out,lock,batch_id):
                dest=out/Path(item['name']).stem/'synthetic'/'output';dest.mkdir(parents=True)
                (dest/'transcript.txt').write_text('Synthetic public test text.')
                (dest/'transcript.json').write_text('{}')
                return {'source_name':item['name'],'source_sha256':item['sha256'],'model_sha256':lock['model']['sha256'],
                    'engine_image':lock['image'],'audio_seconds':1.0,'output_directory':str(dest),
                    'transcript_sha256':trial.sha256(dest/'transcript.txt'),'json_sha256':trial.sha256(dest/'transcript.json')}
            events=[];original_persist=batch.persist_artifacts;original_atomic=batch.atomic_json
            def persist(receipt,boundary):
                events.append('persist-'+receipt['source_name'])
                original_persist(receipt,boundary)
            def atomic(path,value):
                if path.name.endswith('-receipt.json'):
                    events.append('receipt-'+path.name.removesuffix('-receipt.json')+'.wav')
                original_atomic(path,value)
            with patch.object(trial,'read_lock',return_value=lock),patch.object(trial,'engine_guard',side_effect=nullcontext),patch.object(batch,'transcribe',side_effect=fake) as infer,patch.object(batch,'persist_artifacts',side_effect=persist),patch.object(batch,'atomic_json',side_effect=atomic):
                batch.run_batch(path);self.assertEqual(infer.call_count,3)
                for name in batch.NAMES:
                    self.assertLess(events.index('persist-'+name),events.index('receipt-'+name))
                batch.run_batch(path);self.assertEqual(infer.call_count,3)
                output=root/'transcription-batches'/m['batch_id']
                status=json.loads((output/'status.json').read_text())
                self.assertEqual(status['status'],'transcribed-awaiting-editorial-review')
                self.assertEqual(len(status['files']),3)
                one=Path(status['files']['log001.wav']['output_directory'])/'transcript.txt';one.write_text('tampered synthetic text')
                with self.assertRaises(RuntimeError):batch.run_batch(path)
                self.assertEqual(infer.call_count,3)
                self.assertEqual(json.loads((output/'status.json').read_text())['status'],'failed')
            with patch.object(trial,'read_lock',return_value=lock),patch.object(trial,'checked',return_value=b'owned-orphan'),patch.object(batch,'transcribe') as infer:
                with self.assertRaisesRegex(RuntimeError,'requires recovery'):
                    batch.run_batch(path)
                infer.assert_not_called()

    def test_atomic_status_private(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'status.json'
            with patch.object(batch,'sync_directory',wraps=batch.sync_directory) as sync:
                batch.atomic_json(p,{'synthetic':True})
                batch.atomic_json(p,{'synthetic':False})
                self.assertEqual(sync.call_count,2)
                sync.assert_called_with(p.parent)
            self.assertEqual(json.loads(p.read_text()),{'synthetic':False})
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            self.assertEqual(sorted(x.name for x in Path(directory).iterdir()),['status.json'])


if __name__=='__main__':unittest.main()
