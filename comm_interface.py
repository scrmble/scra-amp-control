"""
Communication Interface Abstraction Layer
Provides hardware-agnostic interface for serial communication (UART, RS-485, USB, etc.)
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional, Callable
import time


class CommBackend(Enum):
    """Communication backend types"""
    UART = "UART"
    RS485 = "RS485"
    USB_CDC = "USB_CDC"
    CAN = "CAN"


class CommInterface(ABC):
    """Abstract base class for communication backends"""
    
    @abstractmethod
    def connect(self) -> bool:
        """
        Initialize and connect to the communication backend
        Returns: True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """
        Disconnect and cleanup communication backend
        """
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """
        Check if currently connected
        Returns: True if connected, False otherwise
        """
        pass
    
    @abstractmethod
    def send(self, data: bytes) -> int:
        """
        Send data over the communication interface
        Args:
            data: Bytes to send
        Returns: Number of bytes sent, or -1 on error
        """
        pass
    
    @abstractmethod
    def receive(self, num_bytes: int, timeout: float = 1.0) -> Optional[bytes]:
        """
        Receive data from the communication interface
        Args:
            num_bytes: Number of bytes to receive
            timeout: Timeout in seconds
        Returns: Received bytes, or None on timeout/error
        """
        pass
    
    @abstractmethod
    def flush_rx(self) -> None:
        """
        Flush receive buffer
        """
        pass
    
    @abstractmethod
    def flush_tx(self) -> None:
        """
        Flush transmit buffer
        """
        pass
    
    @abstractmethod
    def get_backend_type(self) -> CommBackend:
        """
        Get backend type identifier
        Returns: Backend type enum
        """
        pass
    
    @abstractmethod
    def get_backend_name(self) -> str:
        """
        Get backend name string (for debugging)
        Returns: Backend name
        """
        pass
    
    @property
    @abstractmethod
    def timeout(self) -> float:
        """Get current timeout in seconds"""
        pass
    
    @timeout.setter
    @abstractmethod
    def timeout(self, value: float) -> None:
        """Set timeout in seconds"""
        pass


class CommManager:
    """
    Communication manager - handles backend selection and provides unified API
    """
    
    def __init__(self, backend: Optional[CommInterface] = None):
        """
        Initialize communication manager
        Args:
            backend: Communication backend instance (optional)
        """
        self._backend: Optional[CommInterface] = backend
        self._is_connected = False
    
    def set_backend(self, backend: CommInterface) -> None:
        """
        Set communication backend
        Args:
            backend: Communication backend instance
        """
        # Disconnect current backend if connected
        if self._backend and self._is_connected:
            self.disconnect()
        
        self._backend = backend
    
    def get_backend(self) -> Optional[CommInterface]:
        """
        Get current communication backend
        Returns: Current backend or None
        """
        return self._backend
    
    def connect(self) -> bool:
        """
        Connect using current backend
        Returns: True if successful, False otherwise
        """
        if not self._backend:
            raise RuntimeError("No backend configured")
        
        self._is_connected = self._backend.connect()
        return self._is_connected
    
    def disconnect(self) -> None:
        """
        Disconnect current backend
        """
        if self._backend and self._is_connected:
            self._backend.disconnect()
            self._is_connected = False
    
    def is_connected(self) -> bool:
        """
        Check if connected
        Returns: True if connected
        """
        if not self._backend:
            return False
        return self._backend.is_connected()
    
    def send(self, data: bytes) -> int:
        """
        Send data using current backend
        Args:
            data: Bytes to send
        Returns: Number of bytes sent
        """
        if not self._backend:
            raise RuntimeError("No backend configured")
        if not self._is_connected:
            raise RuntimeError("Not connected")
        
        return self._backend.send(data)
    
    def receive(self, num_bytes: int, timeout: Optional[float] = None) -> Optional[bytes]:
        """
        Receive data using current backend
        Args:
            num_bytes: Number of bytes to receive
            timeout: Timeout in seconds (None = use backend default)
        Returns: Received bytes or None
        """
        if not self._backend:
            raise RuntimeError("No backend configured")
        if not self._is_connected:
            raise RuntimeError("Not connected")
        
        if timeout is not None:
            return self._backend.receive(num_bytes, timeout)
        return self._backend.receive(num_bytes)
    
    def flush_rx(self) -> None:
        """Flush receive buffer"""
        if self._backend and self._is_connected:
            self._backend.flush_rx()
    
    def flush_tx(self) -> None:
        """Flush transmit buffer"""
        if self._backend and self._is_connected:
            self._backend.flush_tx()
    
    def get_backend_type(self) -> Optional[CommBackend]:
        """Get current backend type"""
        if not self._backend:
            return None
        return self._backend.get_backend_type()
    
    def get_backend_name(self) -> str:
        """Get current backend name"""
        if not self._backend:
            return "None"
        return self._backend.get_backend_name()
    
    @property
    def timeout(self) -> Optional[float]:
        """Get current timeout"""
        if not self._backend:
            return None
        return self._backend.timeout
    
    @timeout.setter
    def timeout(self, value: float) -> None:
        """Set timeout"""
        if not self._backend:
            raise RuntimeError("No backend configured")
        self._backend.timeout = value
    
    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()
        return False
