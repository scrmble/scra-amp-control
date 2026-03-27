"""
RS-485 Communication Backend
Half-duplex serial communication for multi-drop networks
"""

import serial
from typing import Optional
from comm_interface import CommInterface, CommBackend


class RS485Backend(CommInterface):
    """
    RS-485 communication backend
    
    Note: Most USB-to-RS485 adapters handle direction control (DE/RE) automatically.
    This backend is compatible with both:
    - Automatic DE control adapters (most common)
    - Manual DE control (if needed in future)
    """
    
    def __init__(self, port: str, baudrate: int = 115200,
                 timeout: float = 1.0, **kwargs):
        """
        Initialize RS-485 backend
        Args:
            port: Serial port (e.g., 'COM11' or '/dev/ttyUSB0')
            baudrate: Baud rate (default: 115200)
            timeout: Read timeout in seconds (default: 1.0)
            **kwargs: Additional pyserial parameters
        """
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._serial_kwargs = kwargs
        self._serial: Optional[serial.Serial] = None
        
        # Set defaults if not specified
        if 'parity' not in self._serial_kwargs:
            self._serial_kwargs['parity'] = serial.PARITY_NONE
        if 'stopbits' not in self._serial_kwargs:
            self._serial_kwargs['stopbits'] = serial.STOPBITS_ONE
        if 'bytesize' not in self._serial_kwargs:
            self._serial_kwargs['bytesize'] = serial.EIGHTBITS
    
    def connect(self) -> bool:
        """Connect to RS-485 port"""
        try:
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baudrate,
                timeout=self._timeout,
                **self._serial_kwargs
            )
            
            # Flush buffers on connection
            if self._serial.is_open:
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
            
            return self._serial.is_open
            
        except Exception as e:
            print(f"RS-485 connection error: {e}")
            self._serial = None
            return False
    
    def disconnect(self) -> None:
        """Disconnect from RS-485 port"""
        if self._serial and self._serial.is_open:
            # Flush buffers before closing
            try:
                self._serial.flush()
            except Exception:
                pass
            self._serial.close()
        self._serial = None
    
    def is_connected(self) -> bool:
        """Check if connected"""
        return self._serial is not None and self._serial.is_open
    
    def send(self, data: bytes) -> int:
        """
        Send data over RS-485
        Note: USB-to-RS485 adapters typically handle DE control automatically
        """
        if not self.is_connected():
            return -1
        
        try:
            # For RS-485, ensure transmit buffer is flushed
            bytes_written = self._serial.write(data)
            
            # Wait for data to be transmitted
            self._serial.flush()
            
            return bytes_written
            
        except Exception as e:
            print(f"RS-485 send error: {e}")
            return -1
    
    def receive(self, num_bytes: int, timeout: float = 1.0) -> Optional[bytes]:
        """Receive data from RS-485"""
        if not self.is_connected():
            return None
        
        try:
            # Temporarily set timeout if different from current
            old_timeout = self._serial.timeout
            if timeout != old_timeout:
                self._serial.timeout = timeout
            
            data = self._serial.read(num_bytes)
            
            # Restore original timeout
            if timeout != old_timeout:
                self._serial.timeout = old_timeout
            
            return data if len(data) > 0 else None
            
        except Exception as e:
            print(f"RS-485 receive error: {e}")
            return None
    
    def flush_rx(self) -> None:
        """Flush receive buffer"""
        if self.is_connected():
            try:
                self._serial.reset_input_buffer()
            except Exception:
                pass
    
    def flush_tx(self) -> None:
        """Flush transmit buffer"""
        if self.is_connected():
            try:
                self._serial.reset_output_buffer()
            except Exception:
                pass
    
    def get_backend_type(self) -> CommBackend:
        """Get backend type"""
        return CommBackend.RS485
    
    def get_backend_name(self) -> str:
        """Get backend name"""
        return f"RS-485 ({self._port} @ {self._baudrate} baud)"
    
    @property
    def timeout(self) -> float:
        """Get current timeout"""
        if self._serial:
            return self._serial.timeout
        return self._timeout
    
    @timeout.setter
    def timeout(self, value: float) -> None:
        """Set timeout"""
        self._timeout = value
        if self._serial:
            self._serial.timeout = value
    
    @property
    def port(self) -> str:
        """Get port name"""
        return self._port
    
    @property
    def baudrate(self) -> int:
        """Get baudrate"""
        return self._baudrate
