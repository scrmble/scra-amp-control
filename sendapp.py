import argparse
import os
import sys
import time
import struct
import serial

# XMODEM Control Characters
SOH = b'\x01'  # Start of Header (128-byte block)
STX = b'\x02'  # Start of Text (1024-byte block - Used for 1K)
EOT = b'\x04'  # End of Transmission
ACK = b'\x06'  # Acknowledge
NAK = b'\x15'  # Negative Acknowledge
CAN = b'\x18'  # Cancel
CRC_C = b'C'   # CRC request character

def calc_crc16(data: bytes) -> int:
    """Calculate the standard XMODEM CRC-16 (CCITT)."""
    crc = 0
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc

def xmodem_send(port_name: str, baudrate: int, filepath: str) -> bool:
    """Sends a file using the XMODEM-1K protocol. Returns True if the receiver
    confirmed 'Firmware updated!'."""
    if not os.path.exists(filepath):
        print(f"Error: File {filepath} not found.")
        return False

    file_size = os.path.getsize(filepath)
    updated = False  # set True when the bootloader reports 'Firmware updated!'
    print(f"Opening serial port {port_name} at {baudrate} baud...")
    
    try:
        ser = serial.Serial(
            port=port_name,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1
        )
    except Exception as e:
        print(f"Failed to open serial port: {e}")
        return False

    print("Waiting for receiver to initiate transfer (looking for 'C')...")

    response = b''
    # Phase 1: Sync with receiver
    while True:
        if ser.in_waiting > 0:
            response = ser.read(ser.in_waiting)

        # The 'C' may arrive bundled with banner text, so match on containment.
        if CRC_C in response:
            print("Receiver requested CRC mode. Starting transfer...")
            break
        elif CAN in response:
            print("Receiver cancelled transmission.")
            ser.close()
            return False

        if len(response) > 0:
            print(response.decode('utf-8', errors='ignore'), end="")
            response = b''
        time.sleep(0.1)

    # Phase 2: Transmit Data
    packet_number = 1
    bytes_sent = 0

    with open(filepath, 'rb') as f:
        while True:
            # XMODEM-1K uses 1024-byte chunks
            chunk = f.read(1024)
            if not chunk:
                break  # EOF reached

            # Pad final packet with EOF (0x1A) bytes if it is smaller than 1024 bytes
            if len(chunk) < 1024:
                chunk = chunk.ljust(1024, b'\x1A')

            # Calculate packet numbers (wrap around at 255)
            seq = packet_number & 0xFF
            seq_neg = (255 - seq) & 0xFF

            # Compute 16-bit CRC for the 1024-byte payload
            crc = calc_crc16(chunk)
            crc_bytes = struct.pack('>H', crc)  # Big-endian 2 bytes

            # Assemble full packet: [STX] [SEQ] [~SEQ] [1024 BYTES] [CRC MSB] [CRC LSB]
            packet = STX + bytes([seq, seq_neg]) + chunk + crc_bytes

            # Send packet with a retry loop
            retries = 10
            success = False

            # CRITICAL FIX 1: Wipe historical console text before sending the block
            ser.reset_input_buffer() 

            ser.write(packet)
            ser.flush()  # Force data out to hardware buffer

            while retries > 0:
                if ser.in_waiting > 0:
                    response = ser.read(ser.in_waiting)
                else:
                    time.sleep(0.05)
                    continue

                if ACK in response:
                    response = bytearray(response).remove(ACK[0])
                    success = True
                    bytes_sent += len(chunk)
                    print(f"Sent packet {packet_number} successfully. ({bytes_sent}/{file_size} original bytes)")
                    break
                elif NAK in response:
                    response = bytearray(response).remove(NAK[0])
                    retries -= 1
                    print(f"Receiver sent NAK for packet {packet_number}. Retrying ({retries} left)...")
                    ser.write(packet)
                    ser.flush()
                elif CAN in response:
                    response = bytearray(response).remove(CAN[0])
                    print("Receiver canceled transaction via CAN.")
                    ser.close()
                    return False

                if len(response) > 0:
                    print(response.decode('utf-8', errors='ignore'), end="")
                    response = b''

            if not success:
                print("\nToo many errors. Aborting transfer.")
                ser.close()
                return False

            packet_number += 1

    # Phase 3: Finalize Transmission (EOT)
    print("Ending transmission...")
    retries = 10
    ser.write(EOT)
    ser.flush()
    time.sleep(0.05)
    while retries > 0:
        if ser.in_waiting > 0:
            response = ser.read(ser.in_waiting)
        else:
            time.sleep(0.05)
            continue

        if ACK in response:
            print("Transfer complete and acknowledged successfully.")
            if b"Firmware updated" in response:
                updated = True
            response = bytearray(response).remove(ACK[0])
            break

        if len(response) > 0:
            if b"Firmware updated" in response:
                updated = True
            print(response.decode('utf-8', errors='ignore'), end="")
            response = b''

        ser.write(EOT)
        ser.flush()
        retries -= 1
        time.sleep(0.05)

    retries = 100
    while retries > 0:
        if ser.in_waiting > 0:
            response = ser.read(ser.in_waiting)
        else:
            time.sleep(0.05)
            retries -= 1
            continue

        if len(response) > 0:
            if b"Firmware updated" in response:
                updated = True
            print(response.decode('utf-8', errors='ignore'), end="")
            response = b''


    ser.close()
    return updated

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Send a file using XMODEM protocol over a serial port.")
    parser.add_argument("--port", help="Serial port name (e.g., COM3 or /dev/ttyUSB0)")
    parser.add_argument("--data", default="./application/Release/hi-power-amp.app", help="Path to the file you want to send (default: ./application/Release/hi-power-amp.app)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    
    args = parser.parse_args()

    if not os.path.exists(args.data):
        print(f"Error: File '{args.data}' not found.")
        sys.exit(1)

    # A failed transfer leaves the board in the bootloader (boot-config erased on
    # the first packet), so retry the whole upload until it confirms success.
    max_attempts = 4
    ok = False
    for attempt in range(1, max_attempts + 1):
        print(f"\n=== Upload attempt {attempt}/{max_attempts} ===")
        if xmodem_send(args.port, args.baud, args.data):
            ok = True
            break
        if attempt < max_attempts:
            print("No 'Firmware updated!' confirmation - retrying...")
            time.sleep(1.0)

    if not ok:
        print("\nUpload failed after all attempts.")
        sys.exit(1)
