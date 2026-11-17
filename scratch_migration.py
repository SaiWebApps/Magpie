import hashlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
import magpie_paths  # noqa: E402

real = Path.home() / ".config"
names = ["magpie.json", "magpie-jobs.sqlite3", "magpie-ibroadcast.json",
         "magpie-ibroadcast-sent.json", "magpie-ibroadcast-names.json"]
fake_home = Path(tempfile.mkdtemp(prefix="magpie-migration-"))
(fake_home / ".config").mkdir()
digests = {}
for name in names:
    src = real / name
    if src.is_file():
        shutil.copy2(src, fake_home / ".config" / name)
        digests[name] = hashlib.sha256(src.read_bytes()).hexdigest()
print("copied:", sorted(digests))

dirs = magpie_paths.Dirs("darwin", {}, fake_home)
moved = magpie_paths.migrate(dirs)
for old, new in moved:
    same = hashlib.sha256(new.read_bytes()).hexdigest() == digests[old.name]
    print(f"{old.name} -> {new.relative_to(fake_home)}  identical={same}")

import json  # noqa: E402
token = json.loads(dirs.ibroadcast_token.read_text())
print("token loads, has access_token:", "access_token" in token)
db = sqlite3.connect(str(dirs.jobs_db))
print("jobs in moved database:", db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
db.close()
print("second run moves:", magpie_paths.migrate(dirs))
print("left in old folder:", sorted(p.name for p in (fake_home / ".config").iterdir()))
shutil.rmtree(fake_home)
print("real ~/.config untouched:", all((real / n).is_file() for n in digests))
