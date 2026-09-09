#!/usr/bin/env python3
"""
Power Amplifier User GUI
Simplified monitoring and control interface for GaN RF amplifier
Uses power_amp_lib for all device communication
"""

import argparse
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import logging

logger = logging.getLogger('power_amp_gui')

# Exact dependency versions this GUI is validated against. Startup is refused
# on any mismatch to guarantee identical Modbus/serial behavior in the field.
REQUIRED_VERSIONS = {
    "pymodbus": "3.14.0",
    "pyserial": "3.5",
}

# Access-phrase gate for the Protection Overrides tab. This is a warranty/
# foot-gun guard, not a security control.
_OVERRIDE_UNLOCK_PHRASE = "NoWarranty"


def _override_phrase_ok(entered: str) -> bool:
    return entered == _OVERRIDE_UNLOCK_PHRASE

from power_amp_lib import (
    PowerAmplifierController,
    AmplifierStatus,
    ConnectionError,
    CommunicationError,
    ValidationError,
    POWER_GOAL_MIN_DBM,
    POWER_GOAL_MAX_DBM,
    PDISS_LIMIT_MARGIN_W,
    PROTECTION_OVERRIDES
)


class _FirmwareLogRedirector:
    """Minimal stdout stand-in that forwards sendapp prints to a GUI log
    callback so the XMODEM transfer output shows up in the tab."""

    def __init__(self, log_callback):
        self._log = log_callback
        self._buffer = ""

    def write(self, text):
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._log(line)

    def flush(self):
        if self._buffer:
            self._log(self._buffer)
            self._buffer = ""


class PowerAmplifierUserGUI:
    """Simplified user GUI for power amplifier control"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("RF Power Amplifier Monitor")
        self.root.resizable(True, True)
        
        # Controller instance
        self.controller = PowerAmplifierController()
        
        # Monitoring state
        self.monitoring_active = False
        self.monitor_thread = None
        
        # Create GUI
        self.create_widgets()
        
        # Size the window to fit content width so nothing is cropped at startup
        self._fit_startup_size()
        
        # Set up cleanup on window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def _fit_startup_size(self):
        """Set startup geometry to the content's required width (no cropping)."""
        self.root.update_idletasks()
        # Inner content width plus the vertical scrollbar, capped to the screen
        scrollbar_w = 20
        content_w = self.main_container.winfo_reqwidth()
        width = min(content_w + scrollbar_w, self.root.winfo_screenwidth())
        height = min(850, self.root.winfo_screenheight() - 80)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(width, 400)
    
    def on_closing(self):
        """Clean up before closing"""
        self.monitoring_active = False
        try:
            self.canvas.unbind_all("<MouseWheel>")
            self.canvas.unbind_all("<Button-4>")
            self.canvas.unbind_all("<Button-5>")
        except Exception:
            pass
        self.controller.disconnect()
        self.root.destroy()
    
    def _on_mousewheel(self, event):
        """Scroll the canvas with the mouse wheel"""
        if event.num == 4:            # Linux scroll up
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:          # Linux scroll down
            self.canvas.yview_scroll(1, "units")
        else:                         # Windows / macOS
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
    
    def create_widgets(self):
        """Create all GUI widgets"""
        # Scrollable container: canvas + vertical scrollbar
        outer = ttk.Frame(self.root)
        outer.pack(fill=tk.BOTH, expand=True)
        
        self.canvas = tk.Canvas(outer, highlightthickness=0)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vscroll.set)
        
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Main container with padding, hosted inside the canvas
        main_container = ttk.Frame(self.canvas, padding=10)
        self.main_container = main_container
        self.canvas_window = self.canvas.create_window((0, 0), window=main_container, anchor="nw")
        
        # Keep scrollregion in sync with content size
        main_container.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        # Make inner frame track canvas width so widgets fill horizontally
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width)
        )
        
        # Enable mousewheel scrolling
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)
        
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
        
        ttk.Label(comm_frame, text="FW Version:").grid(row=0, column=6, sticky="w", padx=(20, 5))
        self.fw_version_label = ttk.Label(comm_frame, text="---", foreground="gray")
        self.fw_version_label.grid(row=0, column=7, padx=5)
        
        # ===== Tabs: main control page + gated protection overrides =====
        self.notebook = ttk.Notebook(main_container)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        main_tab = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(main_tab, text="Monitor & Control")
        
        prot_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(prot_tab, text="Protection Overrides")
        
        # ===== Body: two equal columns (controls | system status) =====
        body = ttk.Frame(main_tab)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1, uniform="body_cols")
        body.columnconfigure(1, weight=1, uniform="body_cols")
        body.rowconfigure(0, weight=1)
        
        left_panel = ttk.Frame(body)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        right_panel = ttk.Frame(body)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        # ===== Control Frame =====
        control_frame = ttk.LabelFrame(left_panel, text="Control", padding=10)
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
        
        # ===== Software Enable Frame =====
        sw_en_frame = ttk.LabelFrame(left_panel, text="Software Enable (Modbus)", padding=10)
        sw_en_frame.pack(fill=tk.X, pady=(0, 10))

        sw_allow_row = ttk.Frame(sw_en_frame)
        sw_allow_row.pack(fill=tk.X, pady=(0, 5))
        self.sw_en_allow_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sw_allow_row, text="Allow SW Control",
                        variable=self.sw_en_allow_var,
                        command=self.on_sw_en_allow_changed).pack(side=tk.LEFT)
        ttk.Label(sw_allow_row,
                  text="When enabled, the physical nEN pin is ignored.",
                  foreground="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=10)

        sw_cmd_row = ttk.Frame(sw_en_frame)
        sw_cmd_row.pack(fill=tk.X)
        self.sw_en_enable_btn = ttk.Button(sw_cmd_row, text="Enable",
                                           command=lambda: self.sw_en_command(True),
                                           state="disabled")
        self.sw_en_enable_btn.pack(side=tk.LEFT, padx=(0, 5))
        self.sw_en_disable_btn = ttk.Button(sw_cmd_row, text="Disable",
                                            command=lambda: self.sw_en_command(False),
                                            state="disabled")
        self.sw_en_disable_btn.pack(side=tk.LEFT, padx=5)
        ttk.Label(sw_cmd_row, text="State:").pack(side=tk.LEFT, padx=(15, 5))
        self.sw_en_state_label = ttk.Label(sw_cmd_row, text="---")
        self.sw_en_state_label.pack(side=tk.LEFT)

        # ===== Protection Override tab (gated by access phrase) =====
        self._build_protection_tab(prot_tab)
        
        # ===== Device Configuration Frame =====
        config_frame = ttk.LabelFrame(left_panel, text="Device Configuration", padding=10)
        config_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Operating frequency row
        freq_row = ttk.Frame(config_frame)
        freq_row.pack(fill=tk.X, pady=5)
        
        ttk.Label(freq_row, text="Operating Frequency (MHz):").pack(side=tk.LEFT)
        self.operating_freq_var = tk.StringVar(value="---")
        self.operating_freq_entry = ttk.Entry(freq_row, textvariable=self.operating_freq_var, width=10)
        self.operating_freq_entry.pack(side=tk.LEFT, padx=10)
        self.operating_freq_entry.bind('<Return>', lambda e: self.set_operating_frequency())
        
        ttk.Button(freq_row, text="Read", 
                   command=self.read_operating_frequency).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(freq_row, text="Set", 
                   command=self.set_operating_frequency).pack(side=tk.LEFT)
        
        # Mandatory-setup note: the operating frequency drives power calibration,
        # so it must be set correctly before RF is applied.
        ttk.Label(config_frame,
                  text="\u26a0 Required: set the operating frequency to match your signal "
                       "BEFORE applying RF.\nPrecise power measurement and protection depend on it.",
                  foreground="#b00000", justify=tk.LEFT).pack(anchor=tk.W, padx=2, pady=(0, 5))
        
        # Operating bandwidth row
        bw_row = ttk.Frame(config_frame)
        bw_row.pack(fill=tk.X, pady=5)
        
        ttk.Label(bw_row, text="Operating Bandwidth (MHz):").pack(side=tk.LEFT)
        self.operating_bw_var = tk.StringVar(value="---")
        self.operating_bw_entry = ttk.Entry(bw_row, textvariable=self.operating_bw_var, width=10)
        self.operating_bw_entry.pack(side=tk.LEFT, padx=10)
        self.operating_bw_entry.bind('<Return>', lambda e: self.set_operating_bandwidth())
        
        ttk.Button(bw_row, text="Read", 
                   command=self.read_operating_bandwidth).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(bw_row, text="Set", 
                   command=self.set_operating_bandwidth).pack(side=tk.LEFT)
        
        ttk.Label(bw_row, text="(0 = single point)", foreground="gray").pack(side=tk.LEFT, padx=10)
        
        # Modbus address row
        addr_row = ttk.Frame(config_frame)
        addr_row.pack(fill=tk.X, pady=5)
        
        ttk.Label(addr_row, text="Modbus Address:").pack(side=tk.LEFT)
        self.modbus_addr_var = tk.StringVar(value="---")
        self.modbus_addr_entry = ttk.Entry(addr_row, textvariable=self.modbus_addr_var, width=10)
        self.modbus_addr_entry.pack(side=tk.LEFT, padx=10)
        self.modbus_addr_entry.bind('<Return>', lambda e: self.set_modbus_address())
        
        ttk.Button(addr_row, text="Read", 
                   command=self.read_modbus_address).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(addr_row, text="Set", 
                   command=self.set_modbus_address).pack(side=tk.LEFT)
        
        ttk.Label(addr_row, text="(1-247)", foreground="gray").pack(side=tk.LEFT, padx=10)
        
        # Save config row
        save_row = ttk.Frame(config_frame)
        save_row.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(save_row, text="Save Config", 
                   command=self.save_config).pack(side=tk.LEFT, padx=5)
        ttk.Label(save_row, text="(persist frequency / address / settings to flash)", 
                  foreground="gray").pack(side=tk.LEFT, padx=5)
        
        # ===== Firmware Update Frame =====
        self._build_firmware_update(left_panel)

        # ===== Monitoring Frame =====
        monitor_frame = ttk.LabelFrame(right_panel, text="System Status", padding=10)
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
        
        ttk.Label(thermal_frame, text=f"(Temp < 50°C = LOW; Dissipated turns red within {PDISS_LIMIT_MARGIN_W:.0f}W of the Pdiss limit)",
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
            
            # Populate device info fields
            self.read_device_info()
            
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
        self.fw_version_label.config(text="---", foreground="gray")
        self.operating_freq_var.set("---")
        self.operating_bw_var.set("---")
        self.modbus_addr_var.set("---")
        # Protection overrides are volatile; reset the UI on disconnect
        self.prot_unlock_var.set(False)
        self._set_protection_controls_enabled(False)
        for var in self.prot_check_vars.values():
            var.set(False)
    
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
        
        # Dissipated power: red when within PDISS_LIMIT_MARGIN_W of the device Pdiss limit
        self.dissipated_label.config(text=f"{status.dissipated_power_w:.1f} W")
        if status.max_dissipated_power_w > 0 and \
                status.dissipated_power_w >= status.max_dissipated_power_w - PDISS_LIMIT_MARGIN_W:
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
        
        # Software enable override: reflect firmware state, gate the buttons
        self.sw_en_allow_var.set(status.sw_en_allow)
        btn_state = "normal" if status.sw_en_allow else "disabled"
        self.sw_en_enable_btn.config(state=btn_state)
        self.sw_en_disable_btn.config(state=btn_state)
        if status.sw_en_allow:
            self.sw_en_state_label.config(
                text="ENABLED" if status.sw_en_command else "DISABLED",
                foreground="green" if status.sw_en_command else "gray")
        else:
            self.sw_en_state_label.config(text="pin control", foreground="gray")
        
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
    
    def on_sw_en_allow_changed(self):
        """Grant or revoke software control of the amplifier enable."""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            self.sw_en_allow_var.set(False)
            return
        try:
            self.controller.set_sw_enable_allow(self.sw_en_allow_var.get())
        except Exception as e:
            messagebox.showerror("Error", f"Failed to set SW control: {e}")

    def sw_en_command(self, enable: bool):
        """Enable/disable the amplifier via software (requires Allow SW Control)."""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        try:
            self.controller.set_sw_enable(enable)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to command SW enable: {e}")
    
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
    
    # ==================== Firmware Update ====================

    def _build_firmware_update(self, parent):
        """Firmware-update section: upload a prebuilt .app to the board through
        the XMODEM bootloader (sendapp.py). App generation is intentionally not
        exposed in the user sandbox."""
        base = os.path.dirname(os.path.abspath(__file__))
        default_app = os.path.join(base, "..", "build", "Debug", "hi-power-amp.app")
        self.fw_app_var = tk.StringVar(value=os.path.normpath(default_app))

        fw_frame = ttk.LabelFrame(parent, text="Firmware Update", padding=10)
        fw_frame.pack(fill=tk.X, pady=(0, 10))

        path_row = ttk.Frame(fw_frame)
        path_row.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(path_row, text="Image .app:").pack(side=tk.LEFT)
        ttk.Entry(path_row, textvariable=self.fw_app_var).pack(side=tk.LEFT, fill=tk.X,
                                                               expand=True, padx=5)
        ttk.Button(path_row, text="Browse...", command=self._fw_browse).pack(side=tk.LEFT)

        btn_row = ttk.Frame(fw_frame)
        btn_row.pack(fill=tk.X, pady=(0, 5))
        self.fw_upload_reboot_btn = ttk.Button(btn_row, text="Reboot to Bootloader + Upload",
                                               command=lambda: self.upload_firmware(reboot_first=True))
        self.fw_upload_reboot_btn.pack(side=tk.LEFT, padx=(0, 5))
        self.fw_upload_btn = ttk.Button(btn_row, text="Upload (in bootloader)",
                                        command=lambda: self.upload_firmware(reboot_first=False))
        self.fw_upload_btn.pack(side=tk.LEFT, padx=5)

        self.fw_log_text = tk.Text(fw_frame, height=6, wrap="word", state="disabled")
        self.fw_log_text.pack(fill=tk.BOTH, expand=True)

    def _fw_browse(self):
        path = filedialog.askopenfilename(
            filetypes=[("App image", "*.app"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.fw_app_var.get()) or None)
        if path:
            self.fw_app_var.set(path)

    def _fw_append_log(self, text):
        self.fw_log_text.configure(state="normal")
        self.fw_log_text.insert("end", text)
        self.fw_log_text.see("end")
        self.fw_log_text.configure(state="disabled")

    def _fw_log(self, msg):
        """Thread-safe append to the firmware log widget."""
        if not msg.endswith("\n"):
            msg += "\n"
        self.root.after(0, self._fw_append_log, msg)

    def upload_firmware(self, reboot_first=False):
        """Upload the selected .app to the board over XMODEM using sendapp.py.

        When reboot_first is True the device is first rebooted into the bootloader
        via the Modbus MCU-reset register; otherwise the board must already be in
        the bootloader window. The monitoring connection is closed either way so
        sendapp can take over the serial port."""
        import sendapp

        app_path = self.fw_app_var.get()
        if not os.path.exists(app_path):
            messagebox.showerror("Upload", f"Image not found:\n{app_path}")
            return

        port = self.controller.port or self.port_var.get()
        try:
            baud = int(self.baudrate_var.get())
        except ValueError:
            baud = 115200
        if not port:
            messagebox.showerror("Upload", "Enter a COM port before uploading.")
            return

        if reboot_first:
            if not self.controller.is_connected:
                messagebox.showerror("Upload",
                                     "Connect first so the device can be rebooted "
                                     "into the bootloader.")
                return
            try:
                # Fire-and-forget reset: the board reboots into the bootloader
                # and never ACKs, so nothing is read after this - just drop the
                # port and let sendapp reopen it after a short delay.
                self.controller.reboot_to_bootloader()
                self._fw_log("Sent MCU software reset - rebooting into bootloader...")
                self.stop_monitoring()
                self.status_label.config(text="MCU Rebooting...", foreground="orange")
                self.port_btn.config(text="Open Port")
            except Exception as e:
                messagebox.showerror("Upload", f"Failed to reboot into bootloader: {e}")
                return
        elif self.controller.is_connected:
            if not messagebox.askyesno("Upload",
                                       "The port is open for monitoring and must be closed "
                                       "to upload. Close it now?"):
                return
            self.close_port()

        self.fw_upload_btn.config(state="disabled")
        self.fw_upload_reboot_btn.config(state="disabled")
        self._fw_log(f"Uploading {app_path} to {port} @ {baud} baud...")

        def worker():
            redirector = _FirmwareLogRedirector(self._fw_log)
            old_stdout = sys.stdout
            sys.stdout = redirector
            ok = False
            try:
                if reboot_first:
                    print("Reset sent; waiting 2 s for the bootloader to come up...")
                    time.sleep(2.0)
                max_attempts = 4
                for attempt in range(1, max_attempts + 1):
                    print(f"\n=== Upload attempt {attempt}/{max_attempts} ===")
                    if sendapp.xmodem_send(port, baud, app_path):
                        ok = True
                        break
                    if attempt < max_attempts:
                        print("No 'Firmware updated!' confirmation - retrying...")
                        time.sleep(1.0)
            except Exception as e:
                print(f"ERROR: {e}")
            finally:
                redirector.flush()
                sys.stdout = old_stdout

            if ok:
                self.root.after(0, lambda: messagebox.showinfo(
                    "Firmware Update", "Firmware updated successfully."))
            else:
                self.root.after(0, lambda: messagebox.showerror(
                    "Firmware Update", "Upload failed. See the log for details."))
            self.root.after(0, lambda: (
                self.fw_upload_btn.config(state="normal"),
                self.fw_upload_reboot_btn.config(state="normal")))

        threading.Thread(target=worker, daemon=True).start()

    def read_device_info(self):
        """Read firmware version, operating frequency and Modbus address on connect"""
        try:
            fw_version = self.controller.get_firmware_version()
            self.fw_version_label.config(text=f"v{fw_version}", foreground="green")
        except Exception as e:
            self.fw_version_label.config(text="?", foreground="orange")
            logger.warning(f"Failed to read firmware version: {e}")
        
        try:
            freq = self.controller.get_operating_frequency()
            self.operating_freq_var.set(str(freq))
        except Exception as e:
            logger.warning(f"Failed to read operating frequency: {e}")
        
        try:
            bw = self.controller.get_operating_bandwidth()
            self.operating_bw_var.set(str(bw))
        except Exception as e:
            logger.warning(f"Failed to read operating bandwidth: {e}")
        
        try:
            addr = self.controller.get_modbus_address()
            self.modbus_addr_var.set(str(addr))
        except Exception as e:
            logger.warning(f"Failed to read Modbus address: {e}")
    
    def read_operating_frequency(self):
        """Read operating frequency from device"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        try:
            freq = self.controller.get_operating_frequency()
            self.operating_freq_var.set(str(freq))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read operating frequency: {e}")
    
    def set_operating_frequency(self):
        """Set operating frequency on device"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        try:
            freq = int(float(self.operating_freq_var.get()))
        except ValueError:
            messagebox.showerror("Error", "Invalid frequency value")
            return
        
        try:
            self.controller.set_operating_frequency(freq)
            messagebox.showinfo("Success", 
                f"Operating frequency set to {freq} MHz\n"
                f"Use 'Save Config' to persist.")
        except ValidationError as e:
            messagebox.showerror("Error", str(e))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to set operating frequency: {e}")
    
    def read_operating_bandwidth(self):
        """Read operating bandwidth from device"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        try:
            bw = self.controller.get_operating_bandwidth()
            self.operating_bw_var.set(str(bw))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read operating bandwidth: {e}")
    
    def set_operating_bandwidth(self):
        """Set operating bandwidth on device"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        try:
            bw = int(float(self.operating_bw_var.get()))
        except ValueError:
            messagebox.showerror("Error", "Invalid bandwidth value")
            return
        
        try:
            self.controller.set_operating_bandwidth(bw)
            messagebox.showinfo("Success", 
                f"Operating bandwidth set to {bw} MHz\n"
                f"(runtime only - not persisted; resets to 0 on power cycle)")
        except ValidationError as e:
            messagebox.showerror("Error", str(e))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to set operating bandwidth: {e}")
    
    def read_modbus_address(self):
        """Read Modbus address from device"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        try:
            addr = self.controller.get_modbus_address()
            self.modbus_addr_var.set(str(addr))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read Modbus address: {e}")
    
    def set_modbus_address(self):
        """Set Modbus address on device"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        try:
            addr = int(self.modbus_addr_var.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid address value")
            return
        
        try:
            self.controller.set_modbus_address(addr)
            messagebox.showinfo("Success", 
                f"Modbus address changed to {addr}\n"
                f"Use 'Save Config' to persist.")
        except ValidationError as e:
            messagebox.showerror("Error", str(e))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to set Modbus address: {e}")
    
    def save_config(self):
        """Persist configuration to device flash"""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        
        try:
            self.controller.save_config()
            messagebox.showinfo("Success", "Configuration saved to flash")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save config: {e}")

    def _set_protection_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for chk in self.prot_check_widgets.values():
            chk.config(state=state)

    def _build_protection_tab(self, parent):
        """Build the Protection Overrides tab. Everything stays greyed out until
        the operator types the access phrase, then the device-unlock checkbox
        becomes available (which in turn enables the individual overrides)."""
        self.override_tab_unlocked = False

        warning = ttk.Label(
            parent,
            text=("Advanced protection overrides. Disabling safety protections can "
                  "permanently damage the amplifier and voids the warranty. Each "
                  "override is recorded permanently in the device."),
            foreground="#b00000", wraplength=620, justify="left")
        warning.pack(anchor="w", pady=(0, 10))

        gate_frame = ttk.LabelFrame(parent, text="Access", padding=10)
        gate_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(gate_frame, text="Access phrase:").pack(side=tk.LEFT)
        self.override_phrase_var = tk.StringVar()
        self.override_phrase_entry = ttk.Entry(
            gate_frame, textvariable=self.override_phrase_var, show="*", width=22)
        self.override_phrase_entry.pack(side=tk.LEFT, padx=10)
        self.override_phrase_entry.bind('<Return>', lambda e: self.unlock_override_tab())
        self.override_unlock_btn = ttk.Button(
            gate_frame, text="Unlock", command=self.unlock_override_tab)
        self.override_unlock_btn.pack(side=tk.LEFT, padx=5)
        self.override_gate_label = ttk.Label(gate_frame, text="Locked", foreground="gray")
        self.override_gate_label.pack(side=tk.LEFT, padx=10)

        prot_frame = ttk.LabelFrame(parent, text="Protection Override (Advanced)", padding=10)
        prot_frame.pack(fill=tk.X, pady=(0, 10))

        unlock_row = ttk.Frame(prot_frame)
        unlock_row.pack(fill=tk.X, pady=(0, 5))
        self.prot_unlock_var = tk.BooleanVar(value=False)
        self.prot_unlock_chk = ttk.Checkbutton(
            unlock_row, text="Unlock protection overrides (device)",
            variable=self.prot_unlock_var, state="disabled",
            command=self.toggle_protection_unlock)
        self.prot_unlock_chk.pack(side=tk.LEFT)
        ttk.Label(unlock_row,
                  text="Recorded permanently; reset on Save Config / reboot.",
                  foreground="#b00000", font=("Arial", 8)).pack(side=tk.LEFT, padx=10)

        self.prot_check_vars = {}
        self.prot_check_widgets = {}
        for key, _reg, label in PROTECTION_OVERRIDES:
            var = tk.BooleanVar(value=False)
            chk = ttk.Checkbutton(prot_frame, text=label, variable=var, state="disabled",
                                  command=lambda k=key: self.on_protection_override(k))
            chk.pack(anchor="w", padx=20)
            self.prot_check_vars[key] = var
            self.prot_check_widgets[key] = chk

    def unlock_override_tab(self):
        """Verify the access phrase and, if correct, enable the device-unlock control."""
        if _override_phrase_ok(self.override_phrase_var.get()):
            self.override_tab_unlocked = True
            self.prot_unlock_chk.config(state="normal")
            self.override_gate_label.config(text="Unlocked", foreground="#008000")
            self.override_unlock_btn.config(state="disabled")
            self.override_phrase_entry.config(state="disabled")
        else:
            self.override_gate_label.config(text="Incorrect phrase", foreground="#b00000")
            self.override_phrase_var.set("")

    def toggle_protection_unlock(self):
        """Enter/leave protection-override mode."""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            self.prot_unlock_var.set(False)
            return
        if self.prot_unlock_var.get():
            if not messagebox.askyesno(
                    "Unlock Protection Overrides",
                    "Disabling safety protections can damage the amplifier and is "
                    "recorded permanently in the device (warranty).\n\nContinue?"):
                self.prot_unlock_var.set(False)
                return
            try:
                self.controller.unlock_protection()
                self._set_protection_controls_enabled(True)
                self.refresh_protection_overrides()
            except Exception as e:
                self.prot_unlock_var.set(False)
                messagebox.showerror("Error", f"Failed to unlock: {e}")
        else:
            try:
                self.controller.lock_access()
            except Exception as e:
                logger.warning(f"Failed to lock access: {e}")
            self._set_protection_controls_enabled(False)
            for var in self.prot_check_vars.values():
                var.set(False)

    def refresh_protection_overrides(self):
        """Sync override checkboxes to live device state."""
        try:
            state = self.controller.get_protection_overrides()
            for key, var in self.prot_check_vars.items():
                var.set(state.get(key, False))
        except Exception as e:
            logger.warning(f"Failed to read protection overrides: {e}")

    def on_protection_override(self, key):
        """Write one protection-override toggle to the device."""
        if not self.controller.is_connected:
            messagebox.showerror("Error", "Not connected to device")
            return
        try:
            self.controller.set_protection_override(key, self.prot_check_vars[key].get())
        except Exception as e:
            self.prot_check_vars[key].set(not self.prot_check_vars[key].get())
            messagebox.showerror("Error", f"Failed to set override: {e}")


def check_required_versions():
    """Verify exact dependency versions, or refuse to start."""
    try:
        from importlib.metadata import version, PackageNotFoundError
    except ImportError:  # Python < 3.8 fallback
        from importlib_metadata import version, PackageNotFoundError

    problems = []
    for pkg, required in REQUIRED_VERSIONS.items():
        try:
            installed = version(pkg)
        except PackageNotFoundError:
            problems.append(f"{pkg}: not installed (require {required})")
            continue
        if installed != required:
            problems.append(f"{pkg}: found {installed}, require {required}")

    if problems:
        msg = ("Incompatible dependency versions detected.\n\n"
               + "\n".join(problems)
               + "\n\nInstall the exact versions:\n"
               + "    pip install "
               + " ".join(f"{p}=={v}" for p, v in REQUIRED_VERSIONS.items()))
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Dependency Version Error", msg)
            root.destroy()
        except Exception:
            print(msg, file=sys.stderr)
        sys.exit(1)


def main():
    check_required_versions()
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
