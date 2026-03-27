# SCRA-PA-control

Python console and GUI application for monitoring and control of SCRA series GaN RF Power Amplifiers via Modbus RTU over RS-485.

Source code can be used in customer proprietary SW which works with Scramble UA products at no cost, and prohibited for use in systems without them.

## Features

- **Real-time monitoring** of currents, voltages, RF power, temperature, and status flags
- **AGC (Automatic Gain Control)** limit configuration
- **Overcurrent protection** status and reset
- **OCP event counters** monitoring and reset
- **MCU software reset** capability
- Available as **CLI tool** or **GUI application**

## Dependencies

- **Python 3.8+**
- **pymodbus** >= 3.0.0 - Modbus RTU communication
- **pyserial** >= 3.5 - Serial port access

## Installation

```bash
pip install pymodbus pyserial
```

Or using requirements.txt:
```bash
pip install -r requirements.txt
```

## Usage

### GUI Application

```bash
python power_amp_user_gui.py
```

### Command Line Interface

```bash
# Read amplifier status
python power_amp_lib.py --port COM5 --status

# Set AGC limit
python power_amp_lib.py --port COM5 --set-agc-limit 45.0

# Get current AGC limit
python power_amp_lib.py --port COM5 --get-agc-limit

# Reset overcurrent latches
python power_amp_lib.py --port COM5 --reset-ocp

# Reset OCP event counters
python power_amp_lib.py --port COM5 --reset-ocp-counters

# Trigger MCU reset
python power_amp_lib.py --port COM5 --mcu-reset
```

### As a Library

```python
from power_amp_lib import PowerAmplifierController

controller = PowerAmplifierController()
controller.connect("COM5", baudrate=115200)

status = controller.get_status()
print(f"Output Power: {status.rf_output_power_dbm} dBm")
print(f"Temperature: {status.temperature_c} °C")

controller.set_agc_limit(45.0)
controller.disconnect()
```

## File Structure

| File | Description |
|------|-------------|
| `power_amp_lib.py` | Core library and CLI application |
| `power_amp_user_gui.py` | Tkinter-based GUI application |
| `comm_interface.py` | Communication abstraction layer |
| `comm_modbus.py` | Modbus RTU wrapper |
| `comm_rs485.py` | RS-485 backend implementation |

## License

See [LICENSE](LICENSE) for details.
