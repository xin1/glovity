import dataclasses
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from imu761_address_tool import AddressToolApp
from protocol import (StreamParser, build_query_command, crc16_ccitt,
                      decode_hi91, ConfigResponse)
from test_protocol import SAMPLE_ADDRESS_05


def sample(address):
    raw = bytearray(SAMPLE_ADDRESS_05)
    raw[10] = address
    raw[4:6] = crc16_ccitt(raw[:4] + raw[6:]).to_bytes(2, "little")
    return bytes(raw)


class FakePort:
    is_open = True
    def __init__(self):
        self.sent = []
    def write(self, data):
        self.sent.append(data)
        return len(data)
    def reset_input_buffer(self):
        pass
    def flush(self):
        pass
    def close(self):
        self.is_open = False


class QueryProtocolTests(unittest.TestCase):
    def test_vendor_queries(self):
        self.assertEqual(build_query_command(1), bytes.fromhex("68 04 01 03 08"))
        self.assertEqual(build_query_command(2), bytes.fromhex("68 04 02 03 09"))
        self.assertEqual(build_query_command(0), bytes.fromhex("68 04 00 03 07"))
        self.assertEqual(build_query_command(254), bytes.fromhex("68 04 FE 03 05"))
        for address in (-1, 255):
            with self.assertRaises(ValueError):
                build_query_command(address)

    def test_noise_bad_length_echo_and_fragmentation(self):
        parser = StreamParser()
        stream = bytes.fromhex("68 05 83 00 5A A5 FF 0F") + build_query_command(1) + sample(1)
        events = []
        for i in range(0, len(stream), 3):
            events.extend(parser.feed(stream[i:i + 3]))
        self.assertEqual([e.address for e in events], [1])

    def test_bad_ack_is_not_accepted(self):
        parser = StreamParser()
        events = parser.feed(bytes.fromhex("68 05 01 8F FF 00") + sample(2))
        self.assertEqual([e.address for e in events], [2])


class QueryGuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        with patch("imu761_address_tool.list_ports.comports", return_value=[]):
            self.app = AddressToolApp(self.root)
        self.port = FakePort()
        self.app.serial_port = self.port

    def tearDown(self):
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()

    def establish(self, address=1):
        self.app.query_var.set(f"0x{address:02X}")
        self.app.request_current_address()
        self.app._handle_hi91(decode_hi91(sample(address)))

    def test_query_wrong_identity_then_valid(self):
        self.app.query_var.set("0x01")
        self.app.request_current_address()
        self.assertEqual(self.port.sent, [build_query_command(1)])
        self.app._handle_hi91(decode_hi91(sample(2)))
        self.assertIsNone(self.app.current_address)
        self.app._handle_hi91(decode_hi91(sample(1)))
        self.assertEqual(self.app.current_address, 1)
        self.assertEqual(self.app.modify_button["state"], "normal")

    def test_modify_requires_active_query_at_new_address(self):
        self.establish()
        self.app.new_address_var.set("0x02")
        with patch("imu761_address_tool.messagebox.askyesno", return_value=True):
            self.app.modify_address()
        self.assertEqual(self.port.sent[-1], bytes.fromhex("68 05 01 0F 02 17"))
        self.app._handle_hi91(decode_hi91(sample(2)))
        self.assertEqual(self.app.pending_target, 2)  # before verification query
        self.app._advance_operations(self.app.next_verify_at + 0.01)
        self.assertEqual(self.port.sent[-1], build_query_command(2))
        self.app._handle_hi91(decode_hi91(sample(1)))
        self.assertEqual(self.app.pending_target, 2)
        self.app._handle_hi91(decode_hi91(sample(2)))
        self.assertIsNone(self.app.pending_target)
        self.assertEqual(self.app.current_address, 2)
        self.assertEqual(self.app.query_var.get(), "0x02")

    def test_scan_only_queries_and_stops_at_match(self):
        self.app.start_scan()
        self.app._advance_operations(self.app.query_deadline + 0.01)
        self.assertEqual(self.port.sent[-1], build_query_command(0))
        self.app._handle_hi91(decode_hi91(sample(0)))
        count = len(self.port.sent)
        self.app._advance_operations(time.monotonic() + 100)
        self.assertEqual(len(self.port.sent), count)
        self.assertFalse(self.app.scanning)
        self.assertEqual(self.app.current_address, 0)
        self.assertTrue(all(cmd[3] == 3 for cmd in self.port.sent))

    def test_timeout_invalidates_identity(self):
        self.establish()
        self.app.new_address_var.set("0x02")
        with patch("imu761_address_tool.messagebox.askyesno", return_value=True):
            self.app.modify_address()
        self.app._advance_operations(self.app.pending_deadline + 0.1)
        self.assertIsNone(self.app.current_address)
        self.assertIsNone(self.app.pending_target)
        self.assertEqual(self.app.modify_button["state"], "disabled")

    def test_ack_alone_never_confirms_success(self):
        self.establish()
        self.app.new_address_var.set("0x02")
        with patch("imu761_address_tool.messagebox.askyesno", return_value=True):
            self.app.modify_address()
        ack = ConfigResponse(1, 0x8F, 0, True, bytes.fromhex("68 05 01 8F 00 15"))
        self.app._handle_config_response(ack)
        self.assertEqual(self.app.pending_target, 2)
        self.app._handle_config_response(dataclasses.replace(ack, result=255, checksum_valid=False))
        self.assertEqual(self.app.pending_target, 2)
        self.app._handle_config_response(dataclasses.replace(ack, address=33, result=255))
        self.assertEqual(self.app.pending_target, 2)
        self.app._handle_config_response(dataclasses.replace(ack, result=255))
        self.assertIsNone(self.app.pending_target)
        self.assertIsNone(self.app.current_address)

    def test_stopped_scan_and_unsolicited_frames_do_not_enable_write(self):
        self.app.start_scan()
        self.app.stop_scan()
        self.app._handle_hi91(decode_hi91(sample(1)))
        self.assertIsNone(self.app.current_address)


if __name__ == "__main__":
    unittest.main()
