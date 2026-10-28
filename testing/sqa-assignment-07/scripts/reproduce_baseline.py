"""Reproduce the 11 original failures in a newly extracted disposable snapshot."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
target=Path(tempfile.mkdtemp(prefix='sqa-baseline-replay-'))
(target/'sut').mkdir(); (target/'tests').mkdir()
with zipfile.ZipFile(ROOT/'evidence/baseline-source.zip') as archive:
    archive.extractall(target/'sut')
shutil.copy2(ROOT/'tests/test_regressions.py',target/'tests/test_regressions.py')
records=ROOT/'evidence/baseline-replay-records.json'
records.write_text('[]',encoding='utf-8')
env=dict(os.environ,SQA_REGRESSION_RECORD=str(records))
with (ROOT/'evidence/baseline-replay.log').open('w',encoding='utf-8') as log:
    result=subprocess.run([sys.executable,'-X','utf8','-m','pytest','tests/test_regressions.py','-q',
                           '--json-report','--json-report-file='+str(ROOT/'evidence/baseline-replay.json')],
                          cwd=target,env=env,stdout=log,stderr=subprocess.STDOUT)
summary=json.loads((ROOT/'evidence/baseline-replay.json').read_text(encoding='utf-8'))['summary']
if result.returncode != 1 or summary.get('failed') != 11:
    raise SystemExit('基线复现与预期不同，请检查baseline-replay.log。')
print(f'已在独立快照复现11项原版失败，临时源码：{target}')
