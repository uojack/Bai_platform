import importlib.util
from unittest.mock import patch
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("baiplayer_server", MODULE_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.inventory_patch = patch.object(server, "initial_inventory", return_value=json.loads((Path(__file__).parent/"fixtures/inventory.json").read_text()))
        self.inventory_patch.start()
        self.addCleanup(self.inventory_patch.stop)
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = server.Store(Path(self.tempdir.name) / "test.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_seed_preserves_configured_device_inventory(self):
        devices = self.store.devices()
        expected = json.loads((Path(__file__).parent / "fixtures/inventory.json").read_text())
        self.assertEqual({x["id"] for x in expected}, {x["id"] for x in devices})
        self.assertEqual(7, len(self.store.scenes()))
        self.assertTrue(all(not x["address"] or x["address"].startswith("192.0.2.") for x in devices))
        addresses = [x["address"] for x in devices if x["address"]]
        self.assertEqual(len(addresses), len(set(addresses)))

    def test_deployment_can_restore_original_device_state(self):
        deployment = self.store.prepare_deployment("welcome", ["signage-01", "ops-01"], "function:产学研系统组")
        command = self.store.create_command({"type": "play_scene", "targets": deployment["targets"], "sceneId": "welcome"})
        deployed = self.store.finish_deployment(deployment["id"], command["id"], "simulated")
        self.assertEqual("simulated", deployed["status"])
        changed = {item["id"]: item for item in self.store.devices()}
        self.assertEqual("welcome", changed["signage-01"]["current_scene"])
        self.assertEqual("welcome", changed["ops-01"]["current_scene"])
        restored = self.store.restore_deployment(deployment["id"])
        self.assertEqual("restored", restored["deployment"]["status"])
        self.assertEqual("restore_state", restored["command"]["command_type"])
        final = {item["id"]: item for item in self.store.devices()}
        self.assertEqual("ambient", final["signage-01"]["current_scene"])
        self.assertEqual("ambient", final["ops-01"]["current_scene"])

    def test_register_and_heartbeat(self):
        device = self.store.register_device({"deviceId": "test-01", "name": "测试终端", "zoneId": "入口"})
        self.assertEqual("online", device["status"])
        updated = self.store.heartbeat("test-01", {"sceneId": "welcome"})
        self.assertEqual("welcome", updated["current_scene"])

    def test_command_updates_selected_devices_only(self):
        command = self.store.create_command({"type": "play_scene", "targets": ["signage-01"], "sceneId": "product-a"})
        self.assertEqual("dispatched", command["status"])
        devices = {item["id"]: item for item in self.store.devices()}
        self.assertEqual("product-a", devices["signage-01"]["current_scene"])
        self.assertEqual("ambient", devices["signage-02"]["current_scene"])

    def test_field_event_is_auditable(self):
        item = self.store.add_event("BaiField", "visitor.entered_zone", "入口", "UWB-017", .96)
        self.assertEqual("visitor.entered_zone", item["event_type"])
        self.assertEqual("入口", self.store.events(1)[0]["zone_id"])

    def test_create_content_scene(self):
        scene = self.store.create_scene({"id": "new-product", "name": "新品", "title": "新品发布", "kind": "video", "mediaUrl": "/media/new.mp4"})
        self.assertEqual("new-product", scene["id"])
        self.assertEqual("/media/new.mp4", scene["media_url"])

    def test_update_content_scene(self):
        updated = self.store.update_scene("welcome", {
            "name": "参观欢迎", "title": "欢迎某某访问团", "subtitle": "莅临参观指导",
            "kind": "information", "accent": "#42d67c", "mediaUrl": "", "duration": 18,
        })
        self.assertEqual("欢迎某某访问团", updated["title"])
        stored = next(scene for scene in self.store.scenes() if scene["id"] == "welcome")
        self.assertEqual("莅临参观指导", stored["subtitle"])

    def test_update_device_location(self):
        device = self.store.update_device_location("signage-01", "1号弧 1A")
        self.assertEqual("1号弧 1A", device["zone_id"])
        self.assertEqual("1号弧 1A", next(item for item in self.store.devices() if item["id"] == "signage-01")["zone_id"])

    def test_command_acknowledgement(self):
        command = self.store.create_command({"type": "play_scene", "targets": ["signage-01"], "sceneId": "welcome"})
        ack = self.store.acknowledge_command(command["id"], "signage-01", "playing")
        self.assertEqual("playing", ack["status"])

    def test_multi_device_acknowledgements_are_independent(self):
        command = self.store.create_command({"type": "play_scene", "targets": ["signage-01", "signage-02"], "sceneId": "welcome"})
        first = self.store.acknowledge_command(command["id"], "signage-01", "completed")
        second = self.store.acknowledge_command(command["id"], "signage-02", "completed")
        self.assertEqual("received", first["commandStatus"])
        self.assertEqual("completed", second["commandStatus"])

    def test_rejects_unknown_command_type(self):
        with self.assertRaises(ValueError):
            self.store.create_command({"type": "move_motor", "targets": ["signage-01"]})

    def test_display_power_modes(self):
        self.store.create_command({"type": "set_power_mode", "targets": ["signage-01"], "powerMode": "eco"})
        devices = {item["id"]: item for item in self.store.devices()}
        self.assertEqual("eco", devices["signage-01"]["power_mode"])
        self.assertEqual("active", devices["signage-02"]["power_mode"])

    def test_device_profile_and_baifield_coordinates(self):
        original = next(item for item in self.store.devices() if item["id"] == "signage-01")
        payload = {
            "name": "入口屏", "locationName": "入口", "address": "192.0.2.5",
            "coordX": 12.5, "coordY": 8.25, "coordZ": 1.4, "coordinateRef": "20F-plan-v1",
            "positionVerified": True, "addressVerified": True, "audioStatus": "available", "notes": "现场核验",
        }
        updated = self.store.update_device(original["id"], payload)
        self.assertEqual("入口屏", updated["name"])
        self.assertEqual(12.5, updated["coord_x"])
        self.assertTrue(updated["position_verified"])

    def test_initial_admin_login_and_roles(self):
        credential = (Path(self.tempdir.name) / "INITIAL_ADMIN_PASSWORD.txt").read_text(encoding="utf-8")
        password = next(line.split("=", 1)[1] for line in credential.splitlines() if line.startswith("password="))
        token, user = self.store.authenticate("admin", password)
        self.assertEqual("admin", user["role"])
        self.assertEqual("admin", self.store.session_user(token)["username"])
        created = self.store.create_user({"username": "viewer.one", "displayName": "查看者", "role": "viewer", "password": "temporary-123"})
        self.assertEqual("viewer", created["role"])

    def test_rotate_initial_admin_invalidates_old_password_and_sessions(self):
        credential_path = Path(self.tempdir.name) / "INITIAL_ADMIN_PASSWORD.txt"
        old_password = next(
            line.split("=", 1)[1]
            for line in credential_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("password=")
        )
        token, _ = self.store.authenticate("admin", old_password)
        self.store.rotate_initial_admin()
        self.assertIsNone(self.store.session_user(token))
        with self.assertRaises(PermissionError):
            self.store.authenticate("admin", old_password)


class BrokerTests(unittest.TestCase):
    def test_broker_routes_by_device(self):
        broker = server.EventBroker()
        target = broker.subscribe("screen-01")
        broker.publish("screen-01", {"event": "command", "data": {"id": "cmd-1"}})
        self.assertEqual("cmd-1", target.get_nowait()["data"]["id"])


class FieldDecisionTests(unittest.TestCase):
    def setUp(self):
        self.inventory_patch = patch.object(server, "initial_inventory", return_value=json.loads((Path(__file__).parent/"fixtures/inventory.json").read_text()))
        self.inventory_patch.start()
        self.addCleanup(self.inventory_patch.stop)
        self.tempdir = tempfile.TemporaryDirectory()
        with patch.object(server, "DB_PATH", Path(self.tempdir.name) / "field.sqlite3"):
            self.app = server.BaiPlayerApp(server.RuntimeConfig())

    def tearDown(self):
        self.tempdir.cleanup()

    def test_low_confidence_event_does_not_trigger(self):
        result = self.app.accept_field_event({"eventType": "visitor.entered_zone", "zoneId": "入口接待区", "confidence": .62, "payload": {"peopleCount": 1}})
        self.assertEqual("ignored_low_confidence", result["decision"])
        self.assertIsNone(result["command"])

    def test_repeated_event_is_debounced(self):
        zone = "Example room"
        first = self.app.accept_field_event({"eventType": "visitor.entered_zone", "zoneId": zone, "confidence": .95, "payload": {"peopleCount": 1}})
        second = self.app.accept_field_event({"eventType": "visitor.entered_zone", "zoneId": zone, "confidence": .95, "payload": {"peopleCount": 1}})
        self.assertIsNotNone(first["command"])
        self.assertEqual("ignored_cooldown", second["decision"])

    def test_raw_sensor_media_is_rejected(self):
        with self.assertRaises(ValueError):
            self.app.accept_field_event({"eventType": "camera.people_count", "zoneId": "入口接待区", "confidence": .95, "payload": {"image": "raw-data"}})


if __name__ == "__main__":
    unittest.main()
