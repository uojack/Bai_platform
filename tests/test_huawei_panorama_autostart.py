import tempfile
import unittest
from pathlib import Path

from tools.huawei_panorama_autostart import address_for_mac


class AddressForMacTests(unittest.TestCase):
    def test_ignores_stale_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory) / "arp"
            table.write_text(
                "IP address HW type Flags HW address Mask Device\n"
                "192.0.2.5 0x1 0x0 02:00:00:00:00:05 * enp5s0\n"
                "192.0.2.7 0x1 0x2 02:00:00:00:00:05 * enp5s0\n",
                encoding="utf-8",
            )
            self.assertEqual(address_for_mac("02:00:00:00:00:05", table), "192.0.2.7")

    def test_returns_none_when_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory) / "arp"
            table.write_text("IP address HW type Flags HW address Mask Device\n", encoding="utf-8")
            self.assertIsNone(address_for_mac("00:11:22:33:44:55", table))


if __name__ == "__main__":
    unittest.main()
