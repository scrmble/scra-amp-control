#!/usr/bin/env python3
"""
Power Amplifier User GUI
Simplified monitoring and control interface for GaN RF amplifier
Uses power_amp_lib for all device communication
"""

import argparse
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import logging

logger = logging.getLogger('power_amp_gui')

from power_amp_lib import (
    PowerAmplifierController,
    AmplifierStatus,
    ConnectionError,
    CommunicationError,
    ValidationError,
    POWER_GOAL_MIN_DBM,
    POWER_GOAL_MAX_DBM,
    DISSIPATED_POWER_WARNING_W
)


class PowerAmplifierUserGUI:
    """Simplified user GUI for power amplifier control"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("RF Power Amplifier Monitor")
        self.root.geometry("700x850")
        self.root.resizable(True, True)
        
        # Controller instance
        self.controller = PowerAmplifierController()
        
        # Monitoring state
        self.monitoring_active = False
        self.monitor_thread = None
        
        # Create GUI
        self.create_widgets()
        
        # Set up cleanup on window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def on_closing(self):
        """Clean up before closing"""
        self.monitoring_active = False
        self.controller.disconnect()
        self.root.destroy()
    
    def create_widgets(self):
        """Create all GUI widgets"""
        # Main container with padding
        main_container = ttk.Frame(self.root, padding=10)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # ===== Communication Frame =====
        comm_frame = ttk.LabelFrame(main_container, text="Communication", padding=10)
        comm_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Port settings
        ttk.Label(comm_frame, text="COM Port:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.port_var = tk.StringVar(value="COM5")
        port_entry = ttk.Entry(comm_frame, textvariable=self.port_var, width=10)
        port_entry.grid(row=0, column=1, padx=5)
        
        ttk.Label(comm_frame, text="Baud Rate:").grid(row=0, column=2, sticky="w", padx=(20, 5))
        self.baudrate_var = tk.StringVar(value="115200")
        baudrate_combo = ttk.Combobox(comm_frame, textvariable=self.baudrate_var, 
                                       values=["9600", "19200", "38400", "57600", "115200"],
                                       width=10, state="readonly")
        baudrate_combo.grid(row=0, column=3, padx=5)
        
        self.port_btn = ttk.Button(comm_frame, text="Open Port", command=self.toggle_port)
        self.port_btn.grid(row=0, column=4, padx=20)
        
        self.status_label = ttk.Label(comm_frame, text="Disconnected", foreground="gray")
        self.status_label.grid(row=0, column=5, padx=10)
        
        # ===== Control Frame =====
        control_frame = ttk.LabelFrame(main_container, text="Control", padding=10)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        
        # AGC Limit setting
        power_row = ttk.Frame(control_frame)
        power_row.pack(fill=tk.X, pady=5)
        
        ttk.Label(power_row, text="AGC Limit (dBm):").pack(side=tk.LEFT)
        self.agc_limit_var = tk.StringVar(value="45.0")
        self.agc_limit_spinbox = ttk.Spinbox(
            power_row, 
            from_=POWER_GOAL_MIN_DBM, 
            to=POWER_GOAL_MAX_DBM, 
            increment=0.1,
            textvariable=self.agc_limit_var,
            width=10,
            format="%.1f"
        )
        self.agc_limit_spinbox.pack(side=tk.LEFT, padx=10)
        
        # Bind events for auto-set
        self.agc_limit_spinbox.bind('<Return>', lambda e: self.set_agc_limit())
        self.agc_limit_spinbox.bind('<<Increment>>', lambda e: self.root.after(10, self.set_agc_limit))
        self.agc_limit_spinbox.bind('<<Decrement>>', lambda e: self.root.after(10, self.set_agc_limit))
        
        ttk.Button(power_row, text="Set", command=self.set_agc_limit).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(power_row, text=f"(Max: {POWER_GOAL_MAX_DBM} dBm)", 
                  foreground="gray").pack(side=tk.LEFT, padx=10)
        
        # MCU Reset button (in control frame)
        mcu_row = ttk.Frame(control_frame)
        mcu_row.pack(fill=tk.X, pady=10)
        
        ttk.Button(mcu_row, text="MCU Software Reset", 
                   command=self.mcu_software_reset).pack(side=tk.LEFT, padx=5)
        
        # ===== Monitoring Frame =====
        monitor_frame = ttk.LabelFrame(main_container, text="System Status", padding=10)
        monitor_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create two columns
        left_col = ttk.Frame(monitor_frame)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        right_col = ttk.Frame(monitor_frame)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # --- Left Column: Current/Voltage, RF Power & Thermal ---
        # Current & Voltage section
        cv_frame = ttk.LabelFrame(left_col, text="Current & Voltage", padding=10)
        cv_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(cv_frame, text="System Current:").grid(row=0, column=0, sticky="w")
        self.sys_current_label = ttk.Label(cv_frame, text="--- mA")
        self.sys_current_label.grid(row=0, column=1, sticky="w", padx=10)
        
        ttk.Label(cv_frame, text="Stage 3 Current:").grid(row=1, column=0, sticky="w")
        self.stage3_current_label = ttk.Label(cv_frame, text="--- mA")
        self.stage3_current_label.grid(row=1, column=1, sticky="w", padx=10)
        
        ttk.Label(cv_frame, text="Stage 4 Current:").grid(row=2, column=0, sticky="w")
        self.stage4_current_label = ttk.Label(cv_frame, text="--- mA")
        self.stage4_current_label.grid(row=2, column=1, sticky="w", padx=10)
        
        ttk.Label(cv_frame, text="Stage 3 Voltage:").grid(row=3, column=0, sticky="w")
        self.stage3_voltage_label = ttk.Label(cv_frame, text="--- V")
        self.stage3_voltage_label.grid(row=3, column=1, sticky="w", padx=10)
        
        ttk.Label(cv_frame, text="Stage 4 Voltage:").grid(row=4, column=0, sticky="w")
        self.stage4_voltage_label = ttk.Label(cv_frame, text="--- V")
        self.stage4_voltage_label.grid(row=4, column=1, sticky="w", padx=10)
        
        # RF Power section
        rf_frame = ttk.LabelFrame(left_col, text="RF Power", padding=10)
        rf_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(rf_frame, text="Output Power:").grid(row=0, column=0, sticky="w")
        self.rf_out_label = ttk.Label(rf_frame, text="--- dBm", font=("Arial", 11, "bold"))
        self.rf_out_label.grid(row=0, column=1, sticky="w", padx=10)
        
        ttk.Label(rf_frame, text="Reflected Power:").grid(row=1, column=0, sticky="w")
        self.rf_ref_label = ttk.Label(rf_frame, text="--- dBm")
        self.rf_ref_label.grid(row=1, column=1, sticky="w", padx=10)
        
        ttk.Label(rf_frame, text="AGC Limit:").grid(row=2, column=0, sticky="w")
        self.agc_limit_display = ttk.Label(rf_frame, text="--- dBm")
        self.agc_limit_display.grid(row=2, column=1, sticky="w", padx=10)
        
        ttk.Label(rf_frame, text="(Output < 46 dBm = LOW, Reflected < 43 dBm = LOW)", 
                  foreground="gray", font=("Arial", 8)).grid(row=3, column=0, columnspan=2, sticky="w")
        
        # Thermal section
        thermal_frame = ttk.LabelFrame(left_col, text="Thermal", padding=10)
        thermal_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(thermal_frame, text="Temperature:").grid(row=0, column=0, sticky="w")
        self.temp_label = ttk.Label(thermal_frame, text="--- °C", font=("Arial", 11, "bold"))
        self.temp_label.grid(row=0, column=1, sticky="w", padx=10)
        
        ttk.Label(thermal_frame, text="Dissipated Power:").grid(row=1, column=0, sticky="w")
        self.dissipated_label = ttk.Label(thermal_frame, text="--- W", font=("Arial", 12, "bold"))
        self.dissipated_label.grid(row=1, column=1, sticky="w", padx=10)
        
        ttk.Label(thermal_frame, text=f"(Temp < 50°C = LOW, Dissipated > {DISSIPATED_POWER_WARNING_W}W = Warning)", 
                  foreground="gray", font=("Arial", 8)).grid(row=2, column=0, columnspan=2, sticky="w")
        
        # Gate Voltages section
        gate_frame = ttk.LabelFrame(left_col, text="Gate Voltages", padding=10)
        gate_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(gate_frame, text="Gate C3:").grid(row=0, column=0, sticky="w")
        self.gate_c3_label = ttk.Label(gate_frame, text="--- V")
        self.gate_c3_label.grid(row=0, column=1, sticky="w", padx=10)
        
        ttk.Label(gate_frame, text="Gate C4A:").grid(row=1, column=0, sticky="w")
        self.gate_c4a_label = ttk.Label(gate_frame, text="--- V")
        self.gate_c4a_label.grid(row=1, column=1, sticky="w", padx=10)
        
        ttk.Label(gate_frame, text="Gate C4B:").grid(row=2, column=0, sticky="w")
        self.gate_c4b_label = ttk.Label(gate_frame, text="--- V")
        self.gate_c4b_label.grid(row=2, column=1, sticky="w", padx=10)
        
        # --- Right Column: Status Flags & Overcurrent ---
        # Status Flags section
        status_frame = ttk.LabelFrame(right_col, text="Status Flags", padding=10)
        status_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(status_frame, text="OVP:").grid(row=0, column=0, sticky="w")
        self.ovp_label = ttk.Label(status_frame, text="---")
        self.ovp_label.grid(row=0, column=1, sticky="w", padx=10)
        
        ttk.Label(status_frame, text="UVP:").grid(row=1, column=0, sticky="w")
        self.uvp_label = ttk.Label(status_frame, text="---")
        self.uvp_label.grid(row=1, column=1, sticky="w", padx=10)
        
        ttk.Label(status_frame, text="nEn Status:").grid(row=2, column=0, sticky="w")
        self.nen_label = ttk.Label(status_frame, text="---")
        self.nen_label.grid(row=2, column=1, sticky="w", padx=10)
        
        # Overcurrent section
        oc_frame = ttk.LabelFrame(right_col, text="Overcurrent Status", padding=10)
        oc_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(oc_frame, text="System:").grid(row=0, column=0, sticky="w")
        self.oc_sys_label = ttk.Label(oc_frame, text="---")
        self.oc_sys_label.grid(row=0, column=1, sticky="w", padx=10)
        
        ttk.Label(oc_frame, text="Stage 3:").grid(row=1, column=0, sticky="w")
        self.oc_c3_label = ttk.Label(oc_frame, text="---")
        self.oc_c3_label.grid(row=1, column=1, sticky="w", padx=10)
        
        ttk.Label(oc_frame, text="Stage 4:").grid(row=2, column=0, sticky="w")
        self.oc_c4_label = ttk.Label(oc_frame, text="---")
        self.oc_c4_label.grid(row=2, column=1, sticky="w", padx=10)
        
        ttk.Separator(oc_frame, orient='horizontal').grid(row=3, column=0, columnspan=2, 
                                                          sticky="ew", pady=5)
        
        ttk.Label(oc_frame, text="C3 Events:").grid(row=4, column=0, sticky="w")
        self.ocp_c3_count_label = ttk.Label(oc_frame, text="---")
        self.ocp_c3_count_label.grid(row=4, column=1, sticky="w", padx=10)
        
        ttk.Label(oc_frame, text="C4 Events:").grid(row=5, column=0, sticky="w")
        self.ocp_c4_count_label = ttk.Label(oc_frame, text="---")
        self.ocp_c4_count_label.grid(row=5, column=1, sticky="w", padx=10)
        
        # Reset buttons in overcurrent pane
        oc_btn_frame = ttk.Frame(oc_frame)
        oc_btn_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        
        ttk.Button(oc_btn_frame, text="Reset Overcurrent", 
                   command=self.reset_overcurrent).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(oc_btn_frame, text="Reset Counters", 
                   command=self.reset_ocp_counters).pack(side=tk.LEFT)
        
        # AGC Status section
        agc_frame = ttk.LabelFrame(right_col, text="AGC Status", padding=10)
        agc_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(agc_frame, text="AGC Active:").grid(row=0, column=0, sticky="w")
        self.agc_enabled_label = ttk.Label(agc_frame, text="---")
        self.agc_enabled_label.grid(row=0, column=1, sticky="w", padx=10)
        
        ttk.Label(agc_frame, text="Temp Throttling:").grid(row=1, column=0, sticky="w")
        self.agc_throttle_label = ttk.Label(agc_frame, text="---")
        self.agc_throttle_label.grid(row=1, column=1, sticky="w", padx=10)
        
        ttk.Label(agc_frame, text="Overtemp Error:").grid(row=2, column=0, sticky="w")
        self.agc_overtemp_label = ttk.Label(agc_frame, text="---")
        self.agc_overtemp_label.grid(row=2, column=1, sticky="w", padx=10)
        
        ttk.Label(agc_frame, text="Bad SWR:").grid(row=3, column=0, sticky="w")
        self.agc_swr_label = ttk.Label(agc_frame, text="---")
        self.agc_swr_label.grid(row=3, column=1, sticky="w", padx=10)
        
        # Power Enables section (read-only)
        power_en_frame = ttk.LabelFrame(right_col, text="Power Enables", padding=10)
        power_en_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(power_en_frame, text="C1:").grid(row=0, column=0, sticky="w")
        self.power_en_c1_label = ttk.Label(power_en_frame, text="---", width=5)
        self.power_en_c1_label.grid(row=0, column=1, sticky="w", padx=(5, 15))
        
        ttk.Label(power_en_frame, text="C2:").grid(row=0, column=2, sticky="w")
        self.power_en_c2_label = ttk.Label(power_en_frame, text="---", width=5)
        self.power_en_c2_label.grid(row=0, column=3, sticky="w", padx=(5, 15))
        
        ttk.Label(power_en_frame, text="C3:").grid(row=1, column=0, sticky="w")
        self.power_en_c3_label = ttk.Label(power_en_frame, text="---", width=5)
        self.power_en_c3_label.grid(row=1, column=1, sticky="w", padx=(5, 15))
        
        ttk.Label(power_en_frame, text="C4:").grid(row=1, column=2, sticky="w")
        self.power_en_c4_label = ttk.Label(power_en_frame, text="---", width=5)
        self.power_en_c4_label.grid(row=1, column=3, sticky="w", padx=(5, 15))
        
        # Gate Enables section (read-only)
        gate_en_frame = ttk.LabelFrame(right_col, text="Gate Enables", padding=10)
        gate_en_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(gate_en_frame, text="C3:").grid(row=0, column=0, sticky="w")
        self.gate_en_c3_label = ttk.Label(gate_en_frame, text="---", width=5)
        self.gate_en_c3_label.grid(row=0, column=1, sticky="w", padx=(5, 15))
        
        ttk.Label(gate_en_frame, text="C4A:").grid(row=0, column=2, sticky="w")
        self.gate_en_c4a_label = ttk.Label(gate_en_frame, text="---", width=5)
        self.gate_en_c4a_label.grid(row=0, column=3, sticky="w", padx=(5, 15))
        
        ttk.Label(gate_en_frame, text="C4B:").grid(row=0, column=4, sticky="w")
        self.gate_en_c4b_label = ttk.Label(gate_en_frame, text="---", width=5)
        self.gate_en_c4b_label.grid(row=0, column=5, sticky="w", padx=5)
    
    def toggle_port(self):
        """Toggle serial port open/close"""
        if self.controller.is_connected:
            self.close_port()
        else:
            self.open_port()
    
    def open_port(self):
        """Open serial port and start monitoring"""
        try:
            port = self.port_var.get()
            baudrate = int(self.baudrate_var.get())
            
            self.controller.connect(port, baudrate)
            
            self.status_label.config(text="Connected", foreground="green")
            self.port_btn.config(text="Close Port")
            
            # Start monitoring
            self.start_monitoring()
            
        except ConnectionError as e:
            messagebox.showerror("Connection Error", str(e))
            self.status_label.config(text="Disconnected", foreground="gray")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open port: {e}")
            self.status_label.config(text="Disconnected", foreground="gray")
    
    def close_port(self):
        """Close serial port and stop monitoring"""
        self.stop_monitoring()
        self.controller.disconnect()
        self.status_label.config(text="Disconnected", foreground="gray")
        self.port_btn.config(text="Open Port")
    
    def start_monitoring(self):
        """Start the monitoring thread"""
        self.monitoring_active = True
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        """Stop the monitoring thread"""
        self.monitoring_active = False
        self.monitor_thread = None
    
    def monitor_loop(self):
        """Background thread for polling device status"""
        consecutive_errors = 0
        device_online = False
        
        logger.info("Monitor loop started")
        
        while self.monitoring_active and self.controller.is_connected:
            try:
                logger.debug("Polling device status...")
                status = self.controller.get_status()
                consecutive_errors = 0
                
                if not device_online:
                    logger.info("Device came online")
                    self.root.after(0, lambda: self.status_label.config(
                        text="Device Online", foreground="green"))
                    device_online = True
                
                # Update display
                self.root.after(0, lambda s=status: self.update_display(s))
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Monitor loop error (#{consecutive_errors}): {type(e).__name__}: {e}")
                if consecutive_errors >= 3 and device_online:
                    logger.warning("Device went offline after 3 consecutive errors")
                    self.root.after(0, lambda: self.status_label.config(
                        text="Device Offline", foreground="orange"))
                    device_online = False
            
            if not self.monitoring_active:
                break
            
            time.sleep(0.5)
        
        logger.info("Monitor loop ended")
    
    def update_display(self, status: AmplifierStatus):
        """Update all display labels with status data"""
        # Current & Voltage
        self.sys_current_label.config(text=f"{status.system_current_ma:.0f} mA")
        self.stage3_current_label.config(text=f"{status.stage3_current_ma:.0f} mA")
        self.stage4_current_label.config(text=f"{status.stage4_current_ma:.0f} mA")
        self.stage3_voltage_label.config(text=f"{status.stage3_voltage_v:.2f} V")
        self.stage4_voltage_label.config(text=f"{status.stage4_voltage_v:.2f} V")
        
        # RF Power (show LOW if below thresholds)
        if status.rf_output_power_dbm < 46.0:
            self.rf_out_label.config(text="LOW")
        else:
            self.rf_out_label.config(text=f"{status.rf_output_power_dbm:.1f} dBm")
        
        if status.rf_reflected_power_dbm < 43.0:
            self.rf_ref_label.config(text="LOW")
        else:
            self.rf_ref_label.config(text=f"{status.rf_reflected_power_dbm:.1f} dBm")
        
        self.agc_limit_display.config(text=f"{status.agc_limit_dbm:.1f} dBm")
        
        # Update spinbox if not focused
        focused = self.root.focus_get()
        if focused != self.agc_limit_spinbox:
            self.agc_limit_var.set(f"{status.agc_limit_dbm:.1f}")
        
        # Thermal (show LOW if temp below 50C)
        if status.temperature_c < 50.0:
            self.temp_label.config(text="LOW")
        else:
            self.temp_label.config(text=f"{status.temperature_c:.1f} °C")
        
        # Dissipated power with warning coloring
        self.dissipated_label.config(text=f"{status.dissipated_power_w:.1f} W")
        if status.dissipated_power_warning:
            self.dissipated_label.config(foreground="red")
        else:
            self.dissipated_label.config(foreground="black")
        
        # Gate voltages
        self.gate_c3_label.config(text=f"{status.gate_c3_v:.3f} V")
        self.gate_c4a_label.config(text=f"{status.gate_c4a_v:.3f} V")
        self.gate_c4b_label.config(text=f"{status.gate_c4b_v:.3f} V")
        
        # Status flags
        self.ovp_label.config(text="OK" if status.ovp_ok else "FAULT",
                              foreground="green" if status.ovp_ok else "red")
        self.uvp_label.config(text="OK" if status.uvp_ok else "FAULT",
                              foreground="green" if status.uvp_ok else "red")
        self.nen_label.config(text="Enabled" if status.nen_enabled else "Disabled",
                              foreground="green" if status.nen_enabled else "red")
        
        # Overcurrent status
        self.oc_sys_label.config(text="FAULT" if status.overcurrent_sys else "OK",
                                  foreground="red" if status.overcurrent_sys else "green")
        self.oc_c3_label.config(text="FAULT" if status.overcurrent_c3 else "OK",
                                 foreground="red" if status.overcurrent_c3 else "green")
        self.oc_c4_label.config(text="FAULT" if status.overcurrent_c4 else "OK",
                                 foreground="red" if status.overcurrent_c4 else "green")
        
        # OCP counters
        self.ocp_c3_count_label.config(text=str(status.ocp_c3_count))
        self.ocp_c4_count_label.config(text=str(status.ocp_c4_count))
        
        # AGC status
        self.agc_enabled_label.config(
            text="Active" if status.agc_enabled else "Inactive",
            foreground="green" if status.agc_enabled else "gray")
        self.agc_throttle_label.config(
            text="Active" if status.agc_temp_throttling else "Inactive",
            foreground="orange" if status.agc_temp_throttling else "green")
        self.agc_overtemp_label.config(
            text="FAULT" if status.agc_overtemp_error else "OK",
            foreground="red" if status.agc_overtemp_error else "green")
        self.agc_swr_label.config(
            text="FAULT" if status.agc_bad_swr else "OK",
            foreground="red" if status.agc_bad_swr else "green")
        
        # Power enables (read-only display)
        self.power_en_c1_label.config(
            text="ON" if status.power_en_c1 else "OFF",
            foreground="green" if status.power_en_c1 else "gray")
        self.power_en_c2_label.config(
            text="ON" if status.power_en_c2 else "OFF",
            foreground="green" if status.power_en_c2 else "gray")
        self.power_en_c3_label.config(
            text="ON" if status.power_en_c3 else "OFF",
            foreground="green" if status.power_en_c3 else "gray")
        self.power_en_c4_label.config(
            text="ON" if status.power_en_c4 else "OFF",
            foreground="green" if status.power_en_c4 else "gray")
        
        # Gate enables (read-only display)
        self.gate_en_c3_label.config(
            text="ON" if status.gate_en_c3 else "OFF",
            foreground="green" if status.gate_en_c3 else "gray")
        self.gate_en_c4a_label.config(
            text="ON" if status.gate_en_c4a else "OFF",
            foreground="green" if status.gate_en_c4a else "gray")
        self.gate_en_c4b_label.config(
            text="ON" if status.gate_en_c4b else "OFF",
            foreground="green" if status.gate_en_c4b else "gray")
    
    def set_agc_limit(self):
        """Set the AGC limit"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        try:
            power = float(self.agc_limit_var.get())
            
            # Clamp to max
            if power > POWER_GOAL_MAX_DBM:
                power = POWER_GOAL_MAX_DBM
                self.agc_limit_var.set(f"{power:.1f}")
                messagebox.showinfo("Note", f"AGC limit capped at {POWER_GOAL_MAX_DBM} dBm")
            
            self.controller.set_agc_limit(power)
            
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid number")
        except ValidationError as e:
            messagebox.showerror("Validation Error", str(e))
        except (ConnectionError, CommunicationError) as e:
            messagebox.showerror("Error", str(e))
    
    def reset_overcurrent(self):
        """Reset overcurrent latches"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        try:
            self.controller.reset_overcurrent()
            messagebox.showinfo("Success", "Overcurrent latches reset")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to reset overcurrent: {e}")
    
    def reset_ocp_counters(self):
        """Reset OCP event counters"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        try:
            self.controller.reset_ocp_counters()
            messagebox.showinfo("Success", "OCP event counters reset")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to reset OCP counters: {e}")
    
    def mcu_software_reset(self):
        """Trigger MCU software reset"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        if messagebox.askyesno("MCU Reset", 
                               "Are you sure you want to reset the MCU?\n"
                               "The device will reboot."):
            try:
                self.controller.mcu_software_reset()
                self.status_label.config(text="MCU Rebooting...", foreground="orange")
                self.port_btn.config(text="Open Port")
                messagebox.showinfo("MCU Reset", "MCU reset command sent. Device is rebooting.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to reset MCU: {e}")


def main():
    parser = argparse.ArgumentParser(description="Power Amplifier User GUI")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose debug logging")
    args = parser.parse_args()
    
    if args.verbose:
        from power_amp_lib import enable_verbose_logging
        enable_verbose_logging()
    
    root = tk.Tk()
    app = PowerAmplifierUserGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
