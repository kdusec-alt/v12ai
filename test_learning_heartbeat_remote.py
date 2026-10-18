import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import learning_heartbeat_v1084 as heartbeat


class LearningHeartbeatRemoteTests(unittest.TestCase):
    def test_recent_local_write_does_not_claim_verified_remote_storage(self):
        local={"exists":True,"physical_rows":5,"last_time":datetime.now(timezone.utc).isoformat()}
        with patch.object(heartbeat,"_scan_jsonl",return_value=local), patch.object(heartbeat,"memory_diagnostics",return_value={}), patch.object(heartbeat,"storage_status",return_value={"remote_configured":True,"remote_status":"PASS","remote_files":{}}), patch("learning_integrity_v1081.learning_integrity_snapshot",return_value={}):
            row=heartbeat.learning_heartbeat_snapshot(force=True)
        self.assertEqual(row["health_level"],"yellow")
        self.assertFalse(row["remote_verified"])


if __name__ == "__main__":
    unittest.main()
