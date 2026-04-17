import os
import tempfile
import unittest
import shutil
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import memory_store


class TestMemoryStore(unittest.TestCase):
    def setUp(self):
        # Force JSON backend in tests so we don't accidentally write to real PG db
        self.env_patcher = patch.dict("os.environ", {"CIPHER_MEMORY_BACKEND": "json"})
        self.env_patcher.start()
        
        self.tmpdir = tempfile.mkdtemp()
        self.memory_file = os.path.join(self.tmpdir, "jarvis_memory.json")
        self.old_path = os.environ.get("JARVIS_MEMORY_PATH")
        os.environ["JARVIS_MEMORY_PATH"] = self.memory_file
        
        self._orig_decay_interval = memory_store.DECAY_INTERVAL_HOURS
        self._orig_decay_step = memory_store.DECAY_STEP
        self._orig_min_importance = memory_store.MIN_IMPORTANCE
        
        memory_store.DECAY_INTERVAL_HOURS = 24
        memory_store.DECAY_STEP = 0.03
        memory_store.MIN_IMPORTANCE = 0.2
        memory_store.clear_all_memories()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        if self.old_path:
            os.environ["JARVIS_MEMORY_PATH"] = self.old_path
        else:
            os.environ.pop("JARVIS_MEMORY_PATH", None)
            
        memory_store.DECAY_INTERVAL_HOURS = self._orig_decay_interval
        memory_store.DECAY_STEP = self._orig_decay_step
        memory_store.MIN_IMPORTANCE = self._orig_min_importance
        self.env_patcher.stop()

    def test_remember_from_message_and_retrieve(self):
        added = memory_store.remember_from_message("Remember that my name is Rahul and I like chess")
        self.assertGreaterEqual(added, 1)

        memories = memory_store.get_relevant_memories("what do i like", limit=5)
        joined = " ".join(memories).lower()
        self.assertIn("like", joined)

    def test_clear_all_memories(self):
        memory_store.remember_from_message("my favorite editor is vscode")
        stats_before = memory_store.get_memory_stats()
        self.assertGreater(stats_before["count"], 0)

        memory_store.clear_all_memories()
        stats_after = memory_store.get_memory_stats()
        self.assertEqual(stats_after["count"], 0)

    def test_pin_and_unpin_memory(self):
        memory_store.remember_from_message("remember that my project codename is phoenix")
        memories = memory_store.list_memories(limit=10)
        self.assertGreater(len(memories), 0)
        mem_id = memories[0]["id"]

        pinned = memory_store.set_memory_pinned(mem_id, True)
        self.assertTrue(pinned)
        updated = memory_store.list_memories(limit=10)
        target = [m for m in updated if m["id"] == mem_id][0]
        self.assertTrue(target["pinned"])

        unpinned = memory_store.set_memory_pinned(mem_id, False)
        self.assertTrue(unpinned)
        updated2 = memory_store.list_memories(limit=10)
        target2 = [m for m in updated2 if m["id"] == mem_id][0]
        self.assertFalse(target2["pinned"])

    def test_decay_removes_weak_unpinned_but_keeps_pinned(self):
        memory_store.remember_from_message("remember that my weak note is temporary")
        memory_store.remember_from_message("remember that my pinned note is critical")

        memories = memory_store.list_memories(limit=20)
        self.assertGreaterEqual(len(memories), 2)

        weak = next(m for m in memories if "weak note" in m["fact"].lower())
        pinned = next(m for m in memories if "pinned note" in m["fact"].lower())
        memory_store.set_memory_pinned(pinned["id"], True)

        db = memory_store._load_db()
        for mem in db["memories"]:
            if mem["id"] == weak["id"]:
                mem["importance"] = 0.21
            if mem["id"] == pinned["id"]:
                mem["importance"] = 0.21
                mem["pinned"] = True

        db["last_decay_at"] = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        memory_store._save_db(db)

        memory_store.get_memory_stats()
        after = memory_store.list_memories(limit=20)
        after_ids = {m["id"] for m in after}
        self.assertNotIn(weak["id"], after_ids)
        self.assertIn(pinned["id"], after_ids)


if __name__ == "__main__":
    unittest.main()
