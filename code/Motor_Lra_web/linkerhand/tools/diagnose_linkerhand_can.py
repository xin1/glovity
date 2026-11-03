"""Probe a LinkerHand controller without sending motion commands."""

import time

import can


CHANNEL = "PCAN_USBBUS1"
BITRATES = (1_000_000, 500_000, 250_000, 125_000)
HAND_IDS = (0x28, 0x27)


for bitrate in BITRATES:
    bus = None
    try:
        bus = can.Bus(interface="pcan", channel=CHANNEL, bitrate=bitrate)
        for hand_id in HAND_IDS:
            # C1 is a read-only hardware-version request.
            bus.send(can.Message(arbitration_id=hand_id, data=[0xC1], is_extended_id=False))
        messages = [bus.recv(0.1) for _ in range(10)]
        received = [(hex(message.arbitration_id), list(message.data))
                    for message in messages if message is not None]
        print(f"{bitrate}: {received}")
    except Exception as exc:
        print(f"{bitrate}: ERROR {exc}")
    finally:
        if bus is not None:
            bus.shutdown()
    time.sleep(0.2)
