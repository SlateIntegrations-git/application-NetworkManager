#!/usr/bin/env python3
"""
Slate Integrations - IP Route Manager
A modern Tkinter-based GUI application for managing Windows IPv4 routes.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import re
import json
import os
import ctypes
import threading
import itertools
from datetime import datetime
from typing import List, Dict, Optional

try:
    import winreg
    HAS_WINREG = True
except ImportError:
    HAS_WINREG = False

try:
    import serial
    import serial.tools.list_ports
    HAS_PYSERIAL = True
except ImportError:
    HAS_PYSERIAL = False

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False

import ftplib
import socket
from tkinter import filedialog

APP_TITLE = "Slate Integrations - IP Manager"
ADDED_ROUTES_FILE = "added_routes.json"
LOG_FILE = "route_manager.log"

BG_DARK = "#0a0a0a"
BG_CARD = "#141414"
BG_CARD_HOVER = "#1a1a1a"
ACCENT_TEAL = "#14b8a6"
ACCENT_TEAL_HOVER = "#0d9488"
TEXT_WHITE = "#ffffff"
TEXT_GRAY = "#9ca3af"
TEXT_MUTED = "#6b7280"
BORDER_COLOR = "#262626"
GRADIENT_START = "#14b8a6"
GRADIENT_END = "#06b6d4"


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def validate_ipv4(ip: str) -> bool:
    pattern = r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$'
    match = re.match(pattern, ip.strip())
    if not match:
        return False
    for octet in match.groups():
        if int(octet) > 255:
            return False
    return True


def validate_subnet_mask(mask: str) -> bool:
    if not validate_ipv4(mask):
        return False
    octets = [int(x) for x in mask.strip().split('.')]
    binary = ''.join(format(o, '08b') for o in octets)
    if '01' in binary:
        return False
    return True


def log_command(command: str, stdout: str, stderr: str, success: bool):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "SUCCESS" if success else "FAILED"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"[{timestamp}] {status}\n")
        f.write(f"Command: {command}\n")
        if stdout.strip():
            f.write(f"STDOUT:\n{stdout}\n")
        if stderr.strip():
            f.write(f"STDERR:\n{stderr}\n")


def load_added_routes() -> List[Dict]:
    if os.path.exists(ADDED_ROUTES_FILE):
        try:
            with open(ADDED_ROUTES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_added_routes(routes: List[Dict]):
    with open(ADDED_ROUTES_FILE, "w", encoding="utf-8") as f:
        json.dump(routes, f, indent=2)


def discover_serial_ports() -> List[Dict]:
    ports = []
    
    if HAS_PYSERIAL:
        try:
            for port in serial.tools.list_ports.comports():
                ports.append({
                    'device': port.device,
                    'name': port.name or port.device,
                    'description': port.description or 'Unknown Device',
                    'hwid': port.hwid or '',
                    'manufacturer': port.manufacturer or '',
                    'vid': f"{port.vid:04X}" if port.vid else '',
                    'pid': f"{port.pid:04X}" if port.pid else ''
                })
            return ports
        except Exception:
            pass
    
    if HAS_WINREG:
        try:
            path = r'HARDWARE\DEVICEMAP\SERIALCOMM'
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
            for i in itertools.count():
                try:
                    val = winreg.EnumValue(key, i)
                    port_name = str(val[1])
                    device_name = str(val[0]).replace('\\Device\\', '')
                    ports.append({
                        'device': port_name,
                        'name': port_name,
                        'description': device_name,
                        'hwid': '',
                        'manufacturer': '',
                        'vid': '',
                        'pid': ''
                    })
                except EnvironmentError:
                    break
            winreg.CloseKey(key)
        except Exception:
            pass
    
    return ports


class SerialTerminal(tk.Toplevel):
    def __init__(self, parent, port_info: Dict):
        super().__init__(parent)
        self.port_info = port_info
        self.port_name = port_info.get('device', 'COM1')
        self.serial_conn: Optional[serial.Serial] = None
        self.running = False
        self.read_thread = None
        
        self.title(f"Serial Console - {self.port_name}")
        self.geometry("700x500")
        self.configure(bg=BG_DARK)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        self.setup_ui()
        
    def setup_ui(self):
        header = tk.Frame(self, bg=BG_DARK)
        header.pack(fill=tk.X, padx=20, pady=(15, 10))
        
        tk.Label(header, text=f"Port: {self.port_name}", bg=BG_DARK, fg=ACCENT_TEAL, font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        
        desc = self.port_info.get('description', '')
        if desc:
            tk.Label(header, text=f"  ({desc})", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        
        settings_frame = tk.Frame(self, bg=BG_DARK)
        settings_frame.pack(fill=tk.X, padx=20, pady=(0, 10))
        
        tk.Label(settings_frame, text="Baud:", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self.baud_var = tk.StringVar(value="9600")
        baud_combo = ttk.Combobox(settings_frame, textvariable=self.baud_var, values=["9600", "19200", "38400", "57600", "115200"], width=10, state="readonly")
        baud_combo.pack(side=tk.LEFT, padx=(5, 15))
        
        tk.Label(settings_frame, text="Data Bits:", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self.databits_var = tk.StringVar(value="8")
        databits_combo = ttk.Combobox(settings_frame, textvariable=self.databits_var, values=["5", "6", "7", "8"], width=5, state="readonly")
        databits_combo.pack(side=tk.LEFT, padx=(5, 15))
        
        tk.Label(settings_frame, text="Parity:", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self.parity_var = tk.StringVar(value="None")
        parity_combo = ttk.Combobox(settings_frame, textvariable=self.parity_var, values=["None", "Even", "Odd"], width=6, state="readonly")
        parity_combo.pack(side=tk.LEFT, padx=(5, 15))
        
        tk.Label(settings_frame, text="Stop Bits:", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self.stopbits_var = tk.StringVar(value="1")
        stopbits_combo = ttk.Combobox(settings_frame, textvariable=self.stopbits_var, values=["1", "1.5", "2"], width=5, state="readonly")
        stopbits_combo.pack(side=tk.LEFT, padx=(5, 0))
        
        btn_frame = tk.Frame(settings_frame, bg=BG_DARK)
        btn_frame.pack(side=tk.RIGHT)
        
        self.connect_btn = SlateButton(btn_frame, "Connect", command=self.toggle_connection, style="filled", width=100, height=32)
        self.connect_btn.pack(side=tk.LEFT, padx=5)
        
        clear_btn = SlateButton(btn_frame, "Clear", command=self.clear_output, style="outline", width=80, height=32)
        clear_btn.pack(side=tk.LEFT)
        
        terminal_frame = tk.Frame(self, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1)
        terminal_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))
        
        self.output_text = tk.Text(terminal_frame, bg=BG_CARD, fg=TEXT_WHITE, insertbackground=ACCENT_TEAL, font=("Consolas", 10), relief=tk.FLAT, wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(terminal_frame, orient=tk.VERTICAL, command=self.output_text.yview)
        self.output_text.configure(yscrollcommand=scrollbar.set)
        
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.output_text.tag_configure("sent", foreground=ACCENT_TEAL)
        self.output_text.tag_configure("received", foreground=TEXT_WHITE)
        self.output_text.tag_configure("error", foreground="#ef4444")
        self.output_text.tag_configure("info", foreground=TEXT_GRAY)
        
        input_frame = tk.Frame(self, bg=BG_DARK)
        input_frame.pack(fill=tk.X, padx=20, pady=(0, 15))
        
        self.input_entry = tk.Entry(input_frame, bg=BG_CARD, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Consolas", 10), relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER_COLOR, highlightcolor=ACCENT_TEAL)
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        self.input_entry.bind("<Return>", self.send_data)
        
        send_btn = SlateButton(input_frame, "Send", command=self.send_data, style="filled", width=80, height=36)
        send_btn.pack(side=tk.LEFT, padx=(10, 0))
        
        self.append_output("Serial terminal ready. Configure settings and click Connect.\n", "info")
        
        if not HAS_PYSERIAL:
            self.append_output("\nWARNING: pyserial not installed. Install with: pip install pyserial\n", "error")
    
    def append_output(self, text: str, tag: str = "received"):
        self.output_text.insert(tk.END, text, tag)
        self.output_text.see(tk.END)
    
    def clear_output(self):
        self.output_text.delete(1.0, tk.END)
    
    def toggle_connection(self):
        if self.serial_conn and self.serial_conn.is_open:
            self.disconnect()
        else:
            self.connect()
    
    def connect(self):
        if not HAS_PYSERIAL:
            messagebox.showerror("Error", "pyserial is not installed.\nInstall with: pip install pyserial")
            return
        
        try:
            baud = int(self.baud_var.get())
            databits = int(self.databits_var.get())
            parity_map = {"None": serial.PARITY_NONE, "Even": serial.PARITY_EVEN, "Odd": serial.PARITY_ODD}
            parity = parity_map.get(self.parity_var.get(), serial.PARITY_NONE)
            stopbits_map = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE, "2": serial.STOPBITS_TWO}
            stopbits = stopbits_map.get(self.stopbits_var.get(), serial.STOPBITS_ONE)
            
            self.serial_conn = serial.Serial(
                port=self.port_name,
                baudrate=baud,
                bytesize=databits,
                parity=parity,
                stopbits=stopbits,
                timeout=0.1
            )
            
            self.running = True
            self.read_thread = threading.Thread(target=self.read_serial, daemon=True)
            self.read_thread.start()
            
            self.connect_btn.text = "Disconnect"
            self.connect_btn.draw_button()
            self.append_output(f"\nConnected to {self.port_name} @ {baud} baud\n", "info")
            
        except Exception as e:
            messagebox.showerror("Connection Error", str(e))
            self.append_output(f"\nConnection failed: {e}\n", "error")
    
    def disconnect(self):
        self.running = False
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except Exception:
                pass
            self.serial_conn = None
        
        self.connect_btn.text = "Connect"
        self.connect_btn.draw_button()
        self.append_output("\nDisconnected\n", "info")
    
    def read_serial(self):
        while self.running and self.serial_conn and self.serial_conn.is_open:
            try:
                data = self.serial_conn.read(1024)
                if data:
                    text = data.decode('utf-8', errors='replace')
                    self.after(0, lambda t=text: self.append_output(t, "received"))
            except Exception:
                break
    
    def send_data(self, event=None):
        if not self.serial_conn or not self.serial_conn.is_open:
            messagebox.showwarning("Not Connected", "Connect to the serial port first.")
            return
        
        data = self.input_entry.get()
        if data:
            try:
                self.serial_conn.write((data + "\r\n").encode('utf-8'))
                self.append_output(f"> {data}\n", "sent")
                self.input_entry.delete(0, tk.END)
            except Exception as e:
                self.append_output(f"\nSend error: {e}\n", "error")
    
    def on_close(self):
        self.disconnect()
        self.destroy()


class SlateButton(tk.Canvas):
    def __init__(self, parent, text, command=None, style="filled", width=120, height=40, **kwargs):
        super().__init__(parent, width=width, height=height, bg=BG_DARK, highlightthickness=0, **kwargs)
        self.command = command
        self.style = style
        self.text = text
        self.width = width
        self.height = height
        self.hover = False
        
        self.draw_button()
        self.bind("<Button-1>", self.on_click)
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
    
    def draw_button(self):
        self.delete("all")
        
        if self.style == "filled":
            fill_color = ACCENT_TEAL_HOVER if self.hover else ACCENT_TEAL
            self.create_rounded_rect(0, 0, self.width, self.height, 20, fill=fill_color, outline="")
            self.create_text(self.width//2, self.height//2, text=self.text, fill=TEXT_WHITE, font=("Segoe UI", 10, "bold"))
        else:
            outline_color = TEXT_WHITE if self.hover else TEXT_GRAY
            self.create_rounded_rect(2, 2, self.width-2, self.height-2, 20, fill="", outline=outline_color, width=1)
            self.create_text(self.width//2, self.height//2, text=self.text, fill=TEXT_WHITE, font=("Segoe UI", 10))
    
    def create_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = [
            x1+radius, y1, x2-radius, y1,
            x2, y1, x2, y1+radius,
            x2, y2-radius, x2, y2,
            x2-radius, y2, x1+radius, y2,
            x1, y2, x1, y2-radius,
            x1, y1+radius, x1, y1
        ]
        return self.create_polygon(points, smooth=True, **kwargs)
    
    def on_click(self, event):
        if self.command:
            self.command()
    
    def on_enter(self, event):
        self.hover = True
        self.draw_button()
    
    def on_leave(self, event):
        self.hover = False
        self.draw_button()


class FeatureCard(tk.Frame):
    def __init__(self, parent, title, description, **kwargs):
        super().__init__(parent, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1, **kwargs)
        
        self.configure(padx=20, pady=20)
        
        title_label = tk.Label(self, text=title, bg=BG_CARD, fg=ACCENT_TEAL, font=("Segoe UI", 14, "bold"), anchor="w")
        title_label.pack(fill=tk.X, pady=(0, 8))
        
        desc_label = tk.Label(self, text=description, bg=BG_CARD, fg=TEXT_GRAY, font=("Segoe UI", 10), anchor="w", justify=tk.LEFT, wraplength=250)
        desc_label.pack(fill=tk.X)


class RouteManagerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1100x800")
        self.root.minsize(1000, 700)
        self.root.configure(bg=BG_DARK)
        
        self.interfaces: List[Dict] = []
        self.added_routes: List[Dict] = load_added_routes()
        self.is_admin = is_admin()
        self.auto_refresh_enabled = True
        self.auto_refresh_interval = 2000
        self.auto_refresh_job = None
        self.current_view = "routes"
        self.all_routes_data = []
        self.current_filter = "all"
        self.serial_ports: List[Dict] = []
        
        self.setup_styles()
        self.setup_ui()
        self.refresh_interfaces()
        self.refresh_routes()
        self.refresh_serial_ports()
        self.start_auto_refresh()
    
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        
        style.configure("Dark.TFrame", background=BG_DARK)
        style.configure("Card.TFrame", background=BG_CARD)
        
        style.configure("Treeview",
            background=BG_CARD,
            foreground=TEXT_WHITE,
            fieldbackground=BG_CARD,
            borderwidth=0,
            font=("Segoe UI", 10),
            rowheight=45
        )
        style.configure("Treeview.Heading",
            background=BG_DARK,
            foreground=TEXT_GRAY,
            font=("Segoe UI", 10, "bold"),
            borderwidth=0
        )
        style.map("Treeview",
            background=[("selected", ACCENT_TEAL)],
            foreground=[("selected", TEXT_WHITE)]
        )
        
        style.configure("Dark.TCheckbutton",
            background=BG_DARK,
            foreground=TEXT_WHITE,
            font=("Segoe UI", 10)
        )
        style.map("Dark.TCheckbutton", background=[("active", BG_DARK)])
        
        style.configure("Dark.TRadiobutton",
            background=BG_DARK,
            foreground=TEXT_WHITE,
            font=("Segoe UI", 10)
        )
        style.map("Dark.TRadiobutton", background=[("active", BG_DARK)])
    
    def setup_ui(self):
        main_container = tk.Frame(self.root, bg=BG_DARK)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        self.create_header(main_container)
        self.create_hero(main_container)
        self.create_main_content(main_container)
    
    def create_header(self, parent):
        header = tk.Frame(parent, bg=BG_DARK, height=60)
        header.pack(fill=tk.X, padx=40, pady=(20, 0))
        header.pack_propagate(False)
        
        logo_frame = tk.Frame(header, bg=BG_DARK)
        logo_frame.pack(side=tk.LEFT)
        
        slate_label = tk.Label(logo_frame, text="SLATE", bg=BG_DARK, fg=TEXT_WHITE, font=("Segoe UI", 16, "bold"))
        slate_label.pack(side=tk.LEFT)
        
        integrations_label = tk.Label(logo_frame, text="INTEGRATIONS", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10))
        integrations_label.pack(side=tk.LEFT, padx=(8, 0), pady=(4, 0))
        
        nav_frame = tk.Frame(header, bg=BG_DARK)
        nav_frame.pack(side=tk.RIGHT)
        
        if self.is_admin:
            status_text = "Administrator"
            status_color = ACCENT_TEAL
        else:
            status_text = "Limited Mode"
            status_color = TEXT_MUTED
        
        status_label = tk.Label(nav_frame, text=status_text, bg=BG_DARK, fg=status_color, font=("Segoe UI", 10))
        status_label.pack(side=tk.LEFT, padx=20)
        
        interfaces_btn = tk.Label(nav_frame, text="Interfaces", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10), cursor="hand2")
        interfaces_btn.pack(side=tk.LEFT, padx=15)
        interfaces_btn.bind("<Button-1>", lambda e: self.show_interfaces_dialog())
        interfaces_btn.bind("<Enter>", lambda e: interfaces_btn.configure(fg=TEXT_WHITE))
        interfaces_btn.bind("<Leave>", lambda e: interfaces_btn.configure(fg=TEXT_GRAY))
        
        history_btn = tk.Label(nav_frame, text="History", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10), cursor="hand2")
        history_btn.pack(side=tk.LEFT, padx=15)
        history_btn.bind("<Button-1>", lambda e: self.show_history_dialog())
        history_btn.bind("<Enter>", lambda e: history_btn.configure(fg=TEXT_WHITE))
        history_btn.bind("<Leave>", lambda e: history_btn.configure(fg=TEXT_GRAY))
    
    def create_hero(self, parent):
        hero = tk.Frame(parent, bg=BG_DARK)
        hero.pack(fill=tk.X, padx=40, pady=(40, 30))
        
        title_frame = tk.Frame(hero, bg=BG_DARK)
        title_frame.pack()
        
        title1 = tk.Label(title_frame, text="Slate Integrations ", bg=BG_DARK, fg=TEXT_WHITE, font=("Segoe UI", 32, "bold"))
        title1.pack(side=tk.LEFT)
        
        title2 = tk.Label(title_frame, text="IP Manager", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 32))
        title2.pack(side=tk.LEFT)
        
        subtitle = tk.Label(hero, text="Manage your Windows IPv4 routes with ease.\nNo complex configurations, just pure performance.", bg=BG_DARK, fg=TEXT_MUTED, font=("Segoe UI", 11), justify=tk.CENTER)
        subtitle.pack(pady=(15, 25))
        
        btn_frame = tk.Frame(hero, bg=BG_DARK)
        btn_frame.pack()
        
        add_btn = SlateButton(btn_frame, "Add Route", command=self.show_add_route_dialog, style="filled", width=130, height=42)
        add_btn.pack(side=tk.LEFT, padx=10)
        
        delete_btn = SlateButton(btn_frame, "Delete Route", command=self.show_delete_route_dialog, style="outline", width=130, height=42)
        delete_btn.pack(side=tk.LEFT, padx=10)
    
    def create_main_content(self, parent):
        self.content_frame = tk.Frame(parent, bg=BG_DARK)
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=(0, 30))
        
        controls_frame = tk.Frame(self.content_frame, bg=BG_DARK)
        controls_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.main_tabs = {}
        main_tabs_frame = tk.Frame(controls_frame, bg=BG_DARK)
        main_tabs_frame.pack(side=tk.LEFT)
        
        routes_tab = tk.Label(main_tabs_frame, text="Routes", bg=BG_CARD, fg=TEXT_WHITE, font=("Segoe UI", 11, "bold"), padx=20, pady=10, cursor="hand2")
        routes_tab.pack(side=tk.LEFT, padx=(0, 5))
        routes_tab.bind("<Button-1>", lambda e: self.switch_main_view("routes"))
        self.main_tabs["routes"] = routes_tab
        
        console_tab = tk.Label(main_tabs_frame, text="Console", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 11), padx=20, pady=10, cursor="hand2")
        console_tab.pack(side=tk.LEFT, padx=(0, 5))
        console_tab.bind("<Button-1>", lambda e: self.switch_main_view("console"))
        self.main_tabs["console"] = console_tab
        
        nic_tab = tk.Label(main_tabs_frame, text="NIC Config", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 11), padx=20, pady=10, cursor="hand2")
        nic_tab.pack(side=tk.LEFT, padx=(0, 5))
        nic_tab.bind("<Button-1>", lambda e: self.switch_main_view("nic"))
        self.main_tabs["nic"] = nic_tab
        
        transfer_tab = tk.Label(main_tabs_frame, text="File Transfer", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 11), padx=20, pady=10, cursor="hand2")
        transfer_tab.pack(side=tk.LEFT, padx=(0, 20))
        transfer_tab.bind("<Button-1>", lambda e: self.switch_main_view("transfer"))
        self.main_tabs["transfer"] = transfer_tab
        
        self.tab_buttons = {}
        self.route_tabs_frame = tk.Frame(controls_frame, bg=BG_DARK)
        self.route_tabs_frame.pack(side=tk.LEFT)
        
        for tab_id, tab_name in [("all", "All"), ("persistent", "Persistent"), ("temporary", "Temporary")]:
            btn = tk.Label(
                self.route_tabs_frame,
                text=f"{tab_name} (0)",
                bg=BG_CARD if tab_id == "all" else BG_DARK,
                fg=TEXT_WHITE if tab_id == "all" else TEXT_GRAY,
                font=("Segoe UI", 10),
                padx=18,
                pady=10,
                cursor="hand2"
            )
            btn.pack(side=tk.LEFT, padx=(0, 5))
            btn.bind("<Button-1>", lambda e, t=tab_id: self.switch_tab(t))
            self.tab_buttons[tab_id] = btn
        
        right_controls = tk.Frame(controls_frame, bg=BG_DARK)
        right_controls.pack(side=tk.RIGHT)
        
        self.auto_refresh_var = tk.BooleanVar(value=True)
        auto_check = ttk.Checkbutton(
            right_controls,
            text="Auto-refresh",
            variable=self.auto_refresh_var,
            command=self.toggle_auto_refresh,
            style="Dark.TCheckbutton"
        )
        auto_check.pack(side=tk.LEFT, padx=(0, 15))
        
        refresh_btn = SlateButton(right_controls, "Refresh", command=self.refresh_all, style="outline", width=90, height=36)
        refresh_btn.pack(side=tk.LEFT)
        
        self.routes_view = tk.Frame(self.content_frame, bg=BG_DARK)
        self.routes_view.pack(fill=tk.BOTH, expand=True)
        
        table_frame = tk.Frame(self.routes_view, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1)
        table_frame.pack(fill=tk.BOTH, expand=True)
        
        columns = ("destination", "netmask", "gateway", "interface", "metric", "type")
        self.routes_tree = ttk.Treeview(table_frame, columns=columns, show="headings", style="Treeview")
        
        self.routes_tree.heading("destination", text="Destination")
        self.routes_tree.heading("netmask", text="Netmask")
        self.routes_tree.heading("gateway", text="Gateway")
        self.routes_tree.heading("interface", text="Interface")
        self.routes_tree.heading("metric", text="Metric")
        self.routes_tree.heading("type", text="Type")
        
        self.routes_tree.column("destination", width=160, anchor=tk.W)
        self.routes_tree.column("netmask", width=140, anchor=tk.W)
        self.routes_tree.column("gateway", width=140, anchor=tk.W)
        self.routes_tree.column("interface", width=140, anchor=tk.W)
        self.routes_tree.column("metric", width=80, anchor=tk.CENTER)
        self.routes_tree.column("type", width=100, anchor=tk.CENTER)
        
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.routes_tree.yview)
        self.routes_tree.configure(yscrollcommand=scrollbar.set)
        
        self.routes_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.routes_tree.bind("<Double-1>", lambda e: self.show_delete_route_dialog())
        
        self.console_view = tk.Frame(self.content_frame, bg=BG_DARK)
        
        console_header = tk.Frame(self.console_view, bg=BG_DARK)
        console_header.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(console_header, text="Serial Connections", bg=BG_DARK, fg=TEXT_WHITE, font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        tk.Label(console_header, text="Double-click to open terminal", bg=BG_DARK, fg=TEXT_MUTED, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(15, 0))
        
        self.console_count_label = tk.Label(console_header, text="0 ports", bg=BG_DARK, fg=ACCENT_TEAL, font=("Segoe UI", 10))
        self.console_count_label.pack(side=tk.RIGHT)
        
        console_table_frame = tk.Frame(self.console_view, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1)
        console_table_frame.pack(fill=tk.BOTH, expand=True)
        
        console_columns = ("device", "name", "description", "manufacturer", "vid_pid")
        self.console_tree = ttk.Treeview(console_table_frame, columns=console_columns, show="headings", style="Treeview")
        
        self.console_tree.heading("device", text="Port")
        self.console_tree.heading("name", text="Name")
        self.console_tree.heading("description", text="Description")
        self.console_tree.heading("manufacturer", text="Manufacturer")
        self.console_tree.heading("vid_pid", text="VID:PID")
        
        self.console_tree.column("device", width=80, anchor=tk.W)
        self.console_tree.column("name", width=100, anchor=tk.W)
        self.console_tree.column("description", width=300, anchor=tk.W)
        self.console_tree.column("manufacturer", width=150, anchor=tk.W)
        self.console_tree.column("vid_pid", width=100, anchor=tk.CENTER)
        
        console_scrollbar = ttk.Scrollbar(console_table_frame, orient=tk.VERTICAL, command=self.console_tree.yview)
        self.console_tree.configure(yscrollcommand=console_scrollbar.set)
        
        self.console_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)
        console_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.console_tree.bind("<Double-1>", self.open_serial_terminal)
        
        self.nic_view = tk.Frame(self.content_frame, bg=BG_DARK)
        
        nic_header = tk.Frame(self.nic_view, bg=BG_DARK)
        nic_header.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(nic_header, text="Network Adapters", bg=BG_DARK, fg=TEXT_WHITE, font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        tk.Label(nic_header, text="Double-click to configure", bg=BG_DARK, fg=TEXT_MUTED, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(15, 0))
        
        self.nic_count_label = tk.Label(nic_header, text="0 adapters", bg=BG_DARK, fg=ACCENT_TEAL, font=("Segoe UI", 10))
        self.nic_count_label.pack(side=tk.RIGHT)
        
        nic_table_frame = tk.Frame(self.nic_view, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1)
        nic_table_frame.pack(fill=tk.BOTH, expand=True)
        
        nic_columns = ("name", "status", "dhcp", "ip", "subnet", "gateway", "dns")
        self.nic_tree = ttk.Treeview(nic_table_frame, columns=nic_columns, show="headings", style="Treeview")
        
        self.nic_tree.heading("name", text="Adapter")
        self.nic_tree.heading("status", text="Status")
        self.nic_tree.heading("dhcp", text="DHCP")
        self.nic_tree.heading("ip", text="IP Address")
        self.nic_tree.heading("subnet", text="Subnet Mask")
        self.nic_tree.heading("gateway", text="Gateway")
        self.nic_tree.heading("dns", text="DNS Servers")
        
        self.nic_tree.column("name", width=180, anchor=tk.W)
        self.nic_tree.column("status", width=80, anchor=tk.CENTER)
        self.nic_tree.column("dhcp", width=70, anchor=tk.CENTER)
        self.nic_tree.column("ip", width=120, anchor=tk.W)
        self.nic_tree.column("subnet", width=120, anchor=tk.W)
        self.nic_tree.column("gateway", width=120, anchor=tk.W)
        self.nic_tree.column("dns", width=180, anchor=tk.W)
        
        nic_scrollbar = ttk.Scrollbar(nic_table_frame, orient=tk.VERTICAL, command=self.nic_tree.yview)
        self.nic_tree.configure(yscrollcommand=nic_scrollbar.set)
        
        self.nic_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)
        nic_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.nic_tree.bind("<Double-1>", self.show_nic_config_dialog)
        
        self.nic_configs = []
        
        self.transfer_view = tk.Frame(self.content_frame, bg=BG_DARK)
        
        transfer_header = tk.Frame(self.transfer_view, bg=BG_DARK)
        transfer_header.pack(fill=tk.X, pady=(0, 15))
        
        tk.Label(transfer_header, text="File Transfer", bg=BG_DARK, fg=TEXT_WHITE, font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        
        sftp_status = "Available" if HAS_PARAMIKO else "Install paramiko for SFTP/SCP"
        status_color = ACCENT_TEAL if HAS_PARAMIKO else TEXT_MUTED
        tk.Label(transfer_header, text=sftp_status, bg=BG_DARK, fg=status_color, font=("Segoe UI", 10)).pack(side=tk.RIGHT)
        
        transfer_main = tk.Frame(self.transfer_view, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1)
        transfer_main.pack(fill=tk.BOTH, expand=True)
        
        transfer_canvas = tk.Canvas(transfer_main, bg=BG_CARD, highlightthickness=0)
        transfer_scrollbar = ttk.Scrollbar(transfer_main, orient=tk.VERTICAL, command=transfer_canvas.yview)
        self.transfer_scroll_frame = tk.Frame(transfer_canvas, bg=BG_CARD)
        
        self.transfer_scroll_frame.bind("<Configure>", lambda e: transfer_canvas.configure(scrollregion=transfer_canvas.bbox("all")))
        transfer_canvas.create_window((0, 0), window=self.transfer_scroll_frame, anchor="nw")
        transfer_canvas.configure(yscrollcommand=transfer_scrollbar.set)
        
        transfer_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        transfer_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        def _on_mousewheel(event):
            transfer_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        transfer_canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        inner_frame = tk.Frame(self.transfer_scroll_frame, bg=BG_CARD)
        inner_frame.pack(fill=tk.X, padx=25, pady=20)
        
        conn_frame = tk.Frame(inner_frame, bg=BG_CARD)
        conn_frame.pack(fill=tk.X, pady=(0, 20))
        
        tk.Label(conn_frame, text="Connection Settings", bg=BG_CARD, fg=TEXT_WHITE, font=("Segoe UI", 12, "bold")).pack(anchor=tk.W, pady=(0, 15))
        
        protocol_row = tk.Frame(conn_frame, bg=BG_CARD)
        protocol_row.pack(fill=tk.X, pady=5)
        tk.Label(protocol_row, text="Protocol", bg=BG_CARD, fg=TEXT_GRAY, font=("Segoe UI", 10), width=12, anchor=tk.W).pack(side=tk.LEFT)
        
        self.transfer_protocol_var = tk.StringVar(value="FTP")
        protocols = ["FTP", "TFTP", "SFTP", "SCP"]
        protocol_combo = ttk.Combobox(protocol_row, textvariable=self.transfer_protocol_var, values=protocols, state="readonly", width=15)
        protocol_combo.pack(side=tk.LEFT, padx=(10, 0))
        
        host_row = tk.Frame(conn_frame, bg=BG_CARD)
        host_row.pack(fill=tk.X, pady=5)
        tk.Label(host_row, text="Host", bg=BG_CARD, fg=TEXT_GRAY, font=("Segoe UI", 10), width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.transfer_host_entry = tk.Entry(host_row, bg=BG_DARK, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1, width=30)
        self.transfer_host_entry.pack(side=tk.LEFT, ipady=8, padx=(10, 0))
        
        port_row = tk.Frame(conn_frame, bg=BG_CARD)
        port_row.pack(fill=tk.X, pady=5)
        tk.Label(port_row, text="Port", bg=BG_CARD, fg=TEXT_GRAY, font=("Segoe UI", 10), width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.transfer_port_entry = tk.Entry(port_row, bg=BG_DARK, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1, width=10)
        self.transfer_port_entry.insert(0, "21")
        self.transfer_port_entry.pack(side=tk.LEFT, ipady=8, padx=(10, 0))
        
        user_row = tk.Frame(conn_frame, bg=BG_CARD)
        user_row.pack(fill=tk.X, pady=5)
        tk.Label(user_row, text="Username", bg=BG_CARD, fg=TEXT_GRAY, font=("Segoe UI", 10), width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.transfer_user_entry = tk.Entry(user_row, bg=BG_DARK, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1, width=20)
        self.transfer_user_entry.pack(side=tk.LEFT, ipady=8, padx=(10, 0))
        
        pass_row = tk.Frame(conn_frame, bg=BG_CARD)
        pass_row.pack(fill=tk.X, pady=5)
        tk.Label(pass_row, text="Password", bg=BG_CARD, fg=TEXT_GRAY, font=("Segoe UI", 10), width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.transfer_pass_entry = tk.Entry(pass_row, bg=BG_DARK, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1, width=20, show="*")
        self.transfer_pass_entry.pack(side=tk.LEFT, ipady=8, padx=(10, 0))
        
        def update_default_port(*args):
            proto = self.transfer_protocol_var.get()
            ports = {"FTP": "21", "TFTP": "69", "SFTP": "22", "SCP": "22"}
            self.transfer_port_entry.delete(0, tk.END)
            self.transfer_port_entry.insert(0, ports.get(proto, "21"))
        
        self.transfer_protocol_var.trace_add("write", update_default_port)
        
        files_frame = tk.Frame(inner_frame, bg=BG_CARD)
        files_frame.pack(fill=tk.X, pady=(0, 20))
        
        tk.Label(files_frame, text="File Selection", bg=BG_CARD, fg=TEXT_WHITE, font=("Segoe UI", 12, "bold")).pack(anchor=tk.W, pady=(0, 15))
        
        local_row = tk.Frame(files_frame, bg=BG_CARD)
        local_row.pack(fill=tk.X, pady=5)
        tk.Label(local_row, text="Local File", bg=BG_CARD, fg=TEXT_GRAY, font=("Segoe UI", 10), width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.transfer_local_entry = tk.Entry(local_row, bg=BG_DARK, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1, width=40)
        self.transfer_local_entry.pack(side=tk.LEFT, ipady=8, padx=(10, 0))
        SlateButton(local_row, "Browse", command=self.browse_local_file, style="outline", width=80, height=36).pack(side=tk.LEFT, padx=(10, 0))
        
        remote_row = tk.Frame(files_frame, bg=BG_CARD)
        remote_row.pack(fill=tk.X, pady=5)
        tk.Label(remote_row, text="Remote Path", bg=BG_CARD, fg=TEXT_GRAY, font=("Segoe UI", 10), width=12, anchor=tk.W).pack(side=tk.LEFT)
        self.transfer_remote_entry = tk.Entry(remote_row, bg=BG_DARK, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1, width=40)
        self.transfer_remote_entry.pack(side=tk.LEFT, ipady=8, padx=(10, 0))
        SlateButton(remote_row, "Browse", command=self.browse_remote_file, style="outline", width=80, height=36).pack(side=tk.LEFT, padx=(10, 0))
        
        actions_frame = tk.Frame(inner_frame, bg=BG_CARD)
        actions_frame.pack(fill=tk.X, pady=(10, 0))
        
        SlateButton(actions_frame, "Upload", command=self.do_upload, style="filled", width=120, height=42).pack(side=tk.LEFT, padx=(0, 15))
        SlateButton(actions_frame, "Download", command=self.do_download, style="filled", width=120, height=42).pack(side=tk.LEFT, padx=(0, 15))
        SlateButton(actions_frame, "Test Connection", command=self.test_connection, style="outline", width=140, height=42).pack(side=tk.LEFT)
        
        log_frame = tk.Frame(inner_frame, bg=BG_CARD)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(20, 0))
        
        tk.Label(log_frame, text="Transfer Log", bg=BG_CARD, fg=TEXT_WHITE, font=("Segoe UI", 12, "bold")).pack(anchor=tk.W, pady=(0, 10))
        
        self.transfer_log = tk.Text(log_frame, bg=BG_DARK, fg=TEXT_WHITE, font=("Consolas", 10), height=10, relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1, state=tk.DISABLED)
        self.transfer_log.pack(fill=tk.BOTH, expand=True)
        
        self.transfer_log.tag_configure("info", foreground=TEXT_GRAY)
        self.transfer_log.tag_configure("success", foreground=ACCENT_TEAL)
        self.transfer_log.tag_configure("error", foreground="#ef4444")
    
    def switch_main_view(self, view: str):
        self.current_view = view
        
        for vid, tab in self.main_tabs.items():
            if vid == view:
                tab.configure(bg=BG_CARD, fg=TEXT_WHITE, font=("Segoe UI", 11, "bold"))
            else:
                tab.configure(bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 11))
        
        self.routes_view.pack_forget()
        self.console_view.pack_forget()
        self.nic_view.pack_forget()
        self.transfer_view.pack_forget()
        self.route_tabs_frame.pack_forget()
        
        if view == "routes":
            self.routes_view.pack(fill=tk.BOTH, expand=True)
            self.route_tabs_frame.pack(side=tk.LEFT)
        elif view == "console":
            self.console_view.pack(fill=tk.BOTH, expand=True)
            self.refresh_serial_ports()
        elif view == "nic":
            self.nic_view.pack(fill=tk.BOTH, expand=True)
            self.refresh_nic_configs()
        elif view == "transfer":
            self.transfer_view.pack(fill=tk.BOTH, expand=True)
    
    def refresh_serial_ports(self):
        self.serial_ports = discover_serial_ports()
        
        for item in self.console_tree.get_children():
            self.console_tree.delete(item)
        
        for port in self.serial_ports:
            vid_pid = ""
            if port.get('vid') and port.get('pid'):
                vid_pid = f"{port['vid']}:{port['pid']}"
            
            self.console_tree.insert("", tk.END, values=(
                port.get('device', ''),
                port.get('name', ''),
                port.get('description', ''),
                port.get('manufacturer', ''),
                vid_pid
            ))
        
        count = len(self.serial_ports)
        self.console_count_label.configure(text=f"{count} port{'s' if count != 1 else ''}")
    
    def open_serial_terminal(self, event=None):
        selection = self.console_tree.selection()
        if not selection:
            return
        
        item = self.console_tree.item(selection[0])
        values = item['values']
        if not values:
            return
        
        device = values[0]
        port_info = None
        for port in self.serial_ports:
            if port.get('device') == device:
                port_info = port
                break
        
        if port_info:
            SerialTerminal(self.root, port_info)
    
    def log_transfer(self, message: str, tag: str = "info"):
        self.transfer_log.configure(state=tk.NORMAL)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.transfer_log.insert(tk.END, f"[{timestamp}] {message}\n", tag)
        self.transfer_log.see(tk.END)
        self.transfer_log.configure(state=tk.DISABLED)
    
    def browse_local_file(self):
        filename = filedialog.askopenfilename(title="Select Local File")
        if filename:
            self.transfer_local_entry.delete(0, tk.END)
            self.transfer_local_entry.insert(0, filename)
    
    def browse_remote_file(self):
        protocol = self.transfer_protocol_var.get()
        host = self.transfer_host_entry.get().strip()
        
        if not host:
            messagebox.showerror("Error", "Enter host address first")
            return
        
        self.show_remote_browser_dialog()
    
    def show_remote_browser_dialog(self):
        dialog = self.create_dialog("Remote File Browser", 500, 400)
        
        tk.Label(dialog, text="Enter remote path manually:", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10)).pack(pady=(0, 10))
        
        path_entry = tk.Entry(dialog, bg=BG_CARD, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1, width=50)
        path_entry.pack(ipady=8, padx=30)
        path_entry.insert(0, "/")
        
        def use_path():
            path = path_entry.get().strip()
            if path:
                self.transfer_remote_entry.delete(0, tk.END)
                self.transfer_remote_entry.insert(0, path)
                dialog.destroy()
        
        btn_frame = tk.Frame(dialog, bg=BG_DARK)
        btn_frame.pack(pady=20)
        SlateButton(btn_frame, "Use Path", command=use_path, style="filled", width=100, height=40).pack(side=tk.LEFT, padx=10)
        SlateButton(btn_frame, "Cancel", command=dialog.destroy, style="outline", width=100, height=40).pack(side=tk.LEFT, padx=10)
    
    def get_transfer_settings(self):
        return {
            'protocol': self.transfer_protocol_var.get(),
            'host': self.transfer_host_entry.get().strip(),
            'port': int(self.transfer_port_entry.get().strip() or "21"),
            'username': self.transfer_user_entry.get().strip(),
            'password': self.transfer_pass_entry.get(),
            'local_file': self.transfer_local_entry.get().strip(),
            'remote_path': self.transfer_remote_entry.get().strip()
        }
    
    def test_connection(self):
        settings = self.get_transfer_settings()
        
        if not settings['host']:
            messagebox.showerror("Error", "Host address required")
            return
        
        self.log_transfer(f"Testing {settings['protocol']} connection to {settings['host']}:{settings['port']}...", "info")
        
        def do_test():
            try:
                if settings['protocol'] == "FTP":
                    ftp = ftplib.FTP()
                    ftp.connect(settings['host'], settings['port'], timeout=10)
                    if settings['username']:
                        ftp.login(settings['username'], settings['password'])
                    else:
                        ftp.login()
                    ftp.quit()
                    self.root.after(0, lambda: self.log_transfer("FTP connection successful!", "success"))
                
                elif settings['protocol'] == "TFTP":
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.settimeout(5)
                    sock.connect((settings['host'], settings['port']))
                    sock.close()
                    self.root.after(0, lambda: self.log_transfer("TFTP port reachable (UDP)", "success"))
                
                elif settings['protocol'] in ["SFTP", "SCP"]:
                    if not HAS_PARAMIKO:
                        self.root.after(0, lambda: self.log_transfer("paramiko not installed. Run: pip install paramiko", "error"))
                        return
                    
                    transport = paramiko.Transport((settings['host'], settings['port']))
                    transport.connect(username=settings['username'], password=settings['password'])
                    transport.close()
                    self.root.after(0, lambda: self.log_transfer(f"{settings['protocol']} connection successful!", "success"))
                
            except Exception as e:
                self.root.after(0, lambda: self.log_transfer(f"Connection failed: {e}", "error"))
        
        threading.Thread(target=do_test, daemon=True).start()
    
    def do_upload(self):
        settings = self.get_transfer_settings()
        
        if not settings['host']:
            messagebox.showerror("Error", "Host address required")
            return
        if not settings['local_file']:
            messagebox.showerror("Error", "Local file required")
            return
        if not os.path.exists(settings['local_file']):
            messagebox.showerror("Error", "Local file not found")
            return
        
        remote_path = settings['remote_path'] or os.path.basename(settings['local_file'])
        self.log_transfer(f"Uploading {os.path.basename(settings['local_file'])} to {settings['host']}...", "info")
        
        def do_transfer():
            try:
                if settings['protocol'] == "FTP":
                    ftp = ftplib.FTP()
                    ftp.connect(settings['host'], settings['port'], timeout=30)
                    if settings['username']:
                        ftp.login(settings['username'], settings['password'])
                    else:
                        ftp.login()
                    
                    with open(settings['local_file'], 'rb') as f:
                        ftp.storbinary(f"STOR {remote_path}", f)
                    ftp.quit()
                    self.root.after(0, lambda: self.log_transfer("Upload complete!", "success"))
                
                elif settings['protocol'] == "TFTP":
                    self.root.after(0, lambda: self.log_transfer("TFTP upload requires tftpy library", "error"))
                
                elif settings['protocol'] == "SFTP":
                    if not HAS_PARAMIKO:
                        self.root.after(0, lambda: self.log_transfer("paramiko not installed", "error"))
                        return
                    
                    transport = paramiko.Transport((settings['host'], settings['port']))
                    transport.connect(username=settings['username'], password=settings['password'])
                    sftp = paramiko.SFTPClient.from_transport(transport)
                    sftp.put(settings['local_file'], remote_path)
                    sftp.close()
                    transport.close()
                    self.root.after(0, lambda: self.log_transfer("SFTP upload complete!", "success"))
                
                elif settings['protocol'] == "SCP":
                    if not HAS_PARAMIKO:
                        self.root.after(0, lambda: self.log_transfer("paramiko not installed", "error"))
                        return
                    
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(settings['host'], settings['port'], settings['username'], settings['password'])
                    sftp = ssh.open_sftp()
                    sftp.put(settings['local_file'], remote_path)
                    sftp.close()
                    ssh.close()
                    self.root.after(0, lambda: self.log_transfer("SCP upload complete!", "success"))
                
            except Exception as e:
                self.root.after(0, lambda: self.log_transfer(f"Upload failed: {e}", "error"))
        
        threading.Thread(target=do_transfer, daemon=True).start()
    
    def do_download(self):
        settings = self.get_transfer_settings()
        
        if not settings['host']:
            messagebox.showerror("Error", "Host address required")
            return
        if not settings['remote_path']:
            messagebox.showerror("Error", "Remote path required")
            return
        
        local_file = settings['local_file']
        if not local_file:
            local_file = filedialog.asksaveasfilename(
                title="Save Downloaded File",
                initialfile=os.path.basename(settings['remote_path'])
            )
            if not local_file:
                return
            self.transfer_local_entry.delete(0, tk.END)
            self.transfer_local_entry.insert(0, local_file)
        
        self.log_transfer(f"Downloading {settings['remote_path']} from {settings['host']}...", "info")
        
        def do_transfer():
            try:
                if settings['protocol'] == "FTP":
                    ftp = ftplib.FTP()
                    ftp.connect(settings['host'], settings['port'], timeout=30)
                    if settings['username']:
                        ftp.login(settings['username'], settings['password'])
                    else:
                        ftp.login()
                    
                    with open(local_file, 'wb') as f:
                        ftp.retrbinary(f"RETR {settings['remote_path']}", f.write)
                    ftp.quit()
                    self.root.after(0, lambda: self.log_transfer(f"Downloaded to {local_file}", "success"))
                
                elif settings['protocol'] == "TFTP":
                    self.root.after(0, lambda: self.log_transfer("TFTP download requires tftpy library", "error"))
                
                elif settings['protocol'] == "SFTP":
                    if not HAS_PARAMIKO:
                        self.root.after(0, lambda: self.log_transfer("paramiko not installed", "error"))
                        return
                    
                    transport = paramiko.Transport((settings['host'], settings['port']))
                    transport.connect(username=settings['username'], password=settings['password'])
                    sftp = paramiko.SFTPClient.from_transport(transport)
                    sftp.get(settings['remote_path'], local_file)
                    sftp.close()
                    transport.close()
                    self.root.after(0, lambda: self.log_transfer(f"SFTP downloaded to {local_file}", "success"))
                
                elif settings['protocol'] == "SCP":
                    if not HAS_PARAMIKO:
                        self.root.after(0, lambda: self.log_transfer("paramiko not installed", "error"))
                        return
                    
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(settings['host'], settings['port'], settings['username'], settings['password'])
                    sftp = ssh.open_sftp()
                    sftp.get(settings['remote_path'], local_file)
                    sftp.close()
                    ssh.close()
                    self.root.after(0, lambda: self.log_transfer(f"SCP downloaded to {local_file}", "success"))
                
            except Exception as e:
                self.root.after(0, lambda: self.log_transfer(f"Download failed: {e}", "error"))
        
        threading.Thread(target=do_transfer, daemon=True).start()
    
    def refresh_nic_configs(self):
        self.nic_configs = self.discover_nic_configs()
        
        for item in self.nic_tree.get_children():
            self.nic_tree.delete(item)
        
        for nic in self.nic_configs:
            dns_str = ", ".join(nic.get('dns', [])) if nic.get('dns') else ""
            self.nic_tree.insert("", tk.END, values=(
                nic.get('name', ''),
                nic.get('status', ''),
                "DHCP" if nic.get('dhcp') else "Static",
                nic.get('ip', ''),
                nic.get('subnet', ''),
                nic.get('gateway', ''),
                dns_str
            ))
        
        count = len(self.nic_configs)
        self.nic_count_label.configure(text=f"{count} adapter{'s' if count != 1 else ''}")
    
    def discover_nic_configs(self) -> List[Dict]:
        nics = []
        try:
            ps_script = '''
            $adapters = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -or $_.Status -eq 'Disconnected' }
            $result = @()
            foreach ($adapter in $adapters) {
                $ipConfig = Get-NetIPConfiguration -InterfaceIndex $adapter.ifIndex -ErrorAction SilentlyContinue
                $ipAddress = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
                $dhcp = (Get-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).Dhcp
                $dns = (Get-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses
                
                $gateway = ""
                if ($ipConfig.IPv4DefaultGateway) {
                    $gateway = $ipConfig.IPv4DefaultGateway.NextHop
                }
                
                $subnetMask = ""
                if ($ipAddress) {
                    $prefix = $ipAddress.PrefixLength
                    $maskBinary = ('1' * $prefix).PadRight(32, '0')
                    $octets = @()
                    for ($i = 0; $i -lt 4; $i++) {
                        $octets += [Convert]::ToInt32($maskBinary.Substring($i * 8, 8), 2)
                    }
                    $subnetMask = $octets -join '.'
                }
                
                $obj = [PSCustomObject]@{
                    Name = $adapter.Name
                    Status = $adapter.Status
                    DHCP = if ($dhcp -eq 'Enabled') { $true } else { $false }
                    IP = if ($ipAddress) { $ipAddress.IPAddress } else { "" }
                    Subnet = $subnetMask
                    Gateway = $gateway
                    DNS = if ($dns) { $dns } else { @() }
                    Index = $adapter.ifIndex
                }
                $result += $obj
            }
            $result | ConvertTo-Json -Compress
            '''
            
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                if isinstance(data, dict):
                    data = [data]
                
                for item in data:
                    nics.append({
                        'name': item.get('Name', ''),
                        'status': item.get('Status', ''),
                        'dhcp': item.get('DHCP', False),
                        'ip': item.get('IP', ''),
                        'subnet': item.get('Subnet', ''),
                        'gateway': item.get('Gateway', ''),
                        'dns': item.get('DNS', []),
                        'index': item.get('Index', 0)
                    })
        except Exception as e:
            log_command("discover_nic_configs", "", str(e), False)
        
        return nics
    
    def show_nic_config_dialog(self, event=None):
        selection = self.nic_tree.selection()
        if not selection:
            return
        
        item = self.nic_tree.item(selection[0])
        values = item['values']
        if not values:
            return
        
        nic_name = values[0]
        nic_info = None
        for nic in self.nic_configs:
            if nic.get('name') == nic_name:
                nic_info = nic
                break
        
        if not nic_info:
            return
        
        dialog = self.create_dialog(f"Configure: {nic_name}", 500, 520)
        
        main_frame = tk.Frame(dialog, bg=BG_DARK)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=30)
        
        mode_frame = tk.Frame(main_frame, bg=BG_DARK)
        mode_frame.pack(fill=tk.X, pady=(0, 20))
        
        tk.Label(mode_frame, text="IP Configuration", bg=BG_DARK, fg=TEXT_WHITE, font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        
        ip_mode_var = tk.StringVar(value="dhcp" if nic_info.get('dhcp') else "static")
        
        dhcp_radio = ttk.Radiobutton(mode_frame, text="Obtain IP address automatically (DHCP)", variable=ip_mode_var, value="dhcp", style="Dark.TRadiobutton")
        dhcp_radio.pack(anchor=tk.W, pady=(10, 5))
        
        static_radio = ttk.Radiobutton(mode_frame, text="Use static IP address", variable=ip_mode_var, value="static", style="Dark.TRadiobutton")
        static_radio.pack(anchor=tk.W)
        
        static_frame = tk.Frame(main_frame, bg=BG_DARK)
        static_frame.pack(fill=tk.X, pady=(0, 15))
        
        entries = {}
        
        for label_text, key, default in [
            ("IP Address", "ip", nic_info.get('ip', '')),
            ("Subnet Mask", "subnet", nic_info.get('subnet', '')),
            ("Default Gateway", "gateway", nic_info.get('gateway', ''))
        ]:
            row = tk.Frame(static_frame, bg=BG_DARK)
            row.pack(fill=tk.X, pady=5)
            
            tk.Label(row, text=label_text, bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10), width=15, anchor=tk.W).pack(side=tk.LEFT)
            
            entry = tk.Entry(row, bg=BG_CARD, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(10, 0))
            entry.insert(0, default)
            entries[key] = entry
        
        dns_frame = tk.Frame(main_frame, bg=BG_DARK)
        dns_frame.pack(fill=tk.X, pady=(10, 0))
        
        tk.Label(dns_frame, text="DNS Configuration", bg=BG_DARK, fg=TEXT_WHITE, font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        
        dns_mode_var = tk.StringVar(value="auto" if nic_info.get('dhcp') else "manual")
        
        dns_auto_radio = ttk.Radiobutton(dns_frame, text="Obtain DNS automatically", variable=dns_mode_var, value="auto", style="Dark.TRadiobutton")
        dns_auto_radio.pack(anchor=tk.W, pady=(10, 5))
        
        dns_manual_radio = ttk.Radiobutton(dns_frame, text="Use manual DNS servers", variable=dns_mode_var, value="manual", style="Dark.TRadiobutton")
        dns_manual_radio.pack(anchor=tk.W)
        
        dns_entries_frame = tk.Frame(main_frame, bg=BG_DARK)
        dns_entries_frame.pack(fill=tk.X, pady=(10, 0))
        
        dns_list = nic_info.get('dns', [])
        
        for i, (label_text, default) in enumerate([
            ("Primary DNS", dns_list[0] if len(dns_list) > 0 else ""),
            ("Secondary DNS", dns_list[1] if len(dns_list) > 1 else "")
        ]):
            row = tk.Frame(dns_entries_frame, bg=BG_DARK)
            row.pack(fill=tk.X, pady=5)
            
            tk.Label(row, text=label_text, bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10), width=15, anchor=tk.W).pack(side=tk.LEFT)
            
            entry = tk.Entry(row, bg=BG_CARD, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(10, 0))
            entry.insert(0, default)
            entries[f"dns{i+1}"] = entry
        
        def toggle_static_fields(*args):
            state = tk.NORMAL if ip_mode_var.get() == "static" else tk.DISABLED
            for key in ["ip", "subnet", "gateway"]:
                entries[key].configure(state=state)
        
        def toggle_dns_fields(*args):
            state = tk.NORMAL if dns_mode_var.get() == "manual" else tk.DISABLED
            entries["dns1"].configure(state=state)
            entries["dns2"].configure(state=state)
        
        ip_mode_var.trace_add("write", toggle_static_fields)
        dns_mode_var.trace_add("write", toggle_dns_fields)
        
        toggle_static_fields()
        toggle_dns_fields()
        
        def apply_config():
            if not is_admin():
                messagebox.showerror("Error", "Administrator privileges required to change NIC settings.")
                return
            
            use_dhcp = ip_mode_var.get() == "dhcp"
            use_dns_auto = dns_mode_var.get() == "auto"
            
            try:
                if use_dhcp:
                    cmd = f'netsh interface ip set address "{nic_name}" dhcp'
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                    log_command(cmd, result.stdout, result.stderr, result.returncode == 0)
                    
                    if result.returncode != 0:
                        messagebox.showerror("Error", f"Failed to enable DHCP:\n{result.stderr}")
                        return
                else:
                    ip = entries["ip"].get().strip()
                    subnet = entries["subnet"].get().strip()
                    gateway = entries["gateway"].get().strip()
                    
                    if not validate_ipv4(ip):
                        messagebox.showerror("Error", "Invalid IP address")
                        return
                    if not validate_subnet_mask(subnet):
                        messagebox.showerror("Error", "Invalid subnet mask")
                        return
                    if gateway and not validate_ipv4(gateway):
                        messagebox.showerror("Error", "Invalid gateway address")
                        return
                    
                    if gateway:
                        cmd = f'netsh interface ip set address "{nic_name}" static {ip} {subnet} {gateway}'
                    else:
                        cmd = f'netsh interface ip set address "{nic_name}" static {ip} {subnet}'
                    
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                    log_command(cmd, result.stdout, result.stderr, result.returncode == 0)
                    
                    if result.returncode != 0:
                        messagebox.showerror("Error", f"Failed to set static IP:\n{result.stderr}")
                        return
                
                if use_dns_auto:
                    cmd = f'netsh interface ip set dns "{nic_name}" dhcp'
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                    log_command(cmd, result.stdout, result.stderr, result.returncode == 0)
                else:
                    dns1 = entries["dns1"].get().strip()
                    dns2 = entries["dns2"].get().strip()
                    
                    if dns1:
                        if not validate_ipv4(dns1):
                            messagebox.showerror("Error", "Invalid primary DNS address")
                            return
                        cmd = f'netsh interface ip set dns "{nic_name}" static {dns1}'
                        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                        log_command(cmd, result.stdout, result.stderr, result.returncode == 0)
                    
                    if dns2:
                        if not validate_ipv4(dns2):
                            messagebox.showerror("Error", "Invalid secondary DNS address")
                            return
                        cmd = f'netsh interface ip add dns "{nic_name}" {dns2} index=2'
                        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                        log_command(cmd, result.stdout, result.stderr, result.returncode == 0)
                
                messagebox.showinfo("Success", f"Network configuration updated for {nic_name}")
                dialog.destroy()
                self.root.after(2000, self.refresh_nic_configs)
                
            except subprocess.TimeoutExpired:
                messagebox.showerror("Error", "Command timed out")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to apply configuration:\n{e}")
                log_command("apply_nic_config", "", str(e), False)
        
        btn_frame = tk.Frame(dialog, bg=BG_DARK)
        btn_frame.pack(pady=20)
        
        SlateButton(btn_frame, "Apply", command=apply_config, style="filled", width=100, height=40).pack(side=tk.LEFT, padx=10)
        SlateButton(btn_frame, "Cancel", command=dialog.destroy, style="outline", width=100, height=40).pack(side=tk.LEFT, padx=10)
    
    def switch_tab(self, tab_id):
        self.current_filter = tab_id
        for tid, btn in self.tab_buttons.items():
            if tid == tab_id:
                btn.configure(bg=BG_CARD, fg=TEXT_WHITE)
            else:
                btn.configure(bg=BG_DARK, fg=TEXT_GRAY)
        self.filter_routes()
    
    def update_tab_counts(self):
        all_count = len(self.all_routes_data)
        persistent_count = sum(1 for r in self.all_routes_data if r.get('persistent') == 'Yes')
        temporary_count = sum(1 for r in self.all_routes_data if r.get('persistent') == 'No')
        
        self.tab_buttons["all"].configure(text=f"All ({all_count})")
        self.tab_buttons["persistent"].configure(text=f"Persistent ({persistent_count})")
        self.tab_buttons["temporary"].configure(text=f"Temporary ({temporary_count})")
    
    def filter_routes(self):
        for item in self.routes_tree.get_children():
            self.routes_tree.delete(item)
        
        for route in self.all_routes_data:
            if self.current_filter == "persistent" and route.get('persistent') != 'Yes':
                continue
            if self.current_filter == "temporary" and route.get('persistent') != 'No':
                continue
            
            persistent_text = route.get('persistent', 'Unknown')
            type_display = "PERSISTENT" if persistent_text == "Yes" else ("TEMPORARY" if persistent_text == "No" else "SYSTEM")
            
            self.routes_tree.insert("", tk.END, values=(
                route.get('destination', ''),
                route.get('netmask', ''),
                route.get('gateway', ''),
                route.get('interface', ''),
                route.get('metric', ''),
                type_display
            ))
    
    def create_dialog(self, title, width=480, height=400):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry(f"{width}x{height}")
        dialog.configure(bg=BG_DARK)
        dialog.transient(self.root)
        dialog.grab_set()
        
        dialog_title = tk.Label(dialog, text=title, bg=BG_DARK, fg=TEXT_WHITE, font=("Segoe UI", 18, "bold"))
        dialog_title.pack(pady=(30, 25))
        
        return dialog
    
    def show_add_route_dialog(self):
        if not self.is_admin:
            messagebox.showerror("Administrator Required", "Run as Administrator to add routes.")
            return
        
        dialog = self.create_dialog("Add New Route", 500, 450)
        
        form_frame = tk.Frame(dialog, bg=BG_DARK)
        form_frame.pack(fill=tk.X, padx=40)
        
        entries = {}
        selected_interface_index = tk.StringVar()
        
        for label_text, field_id in [("Destination", "dest"), ("Subnet Mask", "mask"), ("Gateway", "gateway")]:
            row = tk.Frame(form_frame, bg=BG_DARK)
            row.pack(fill=tk.X, pady=8)
            
            lbl = tk.Label(row, text=label_text, bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10), width=12, anchor="w")
            lbl.pack(side=tk.LEFT)
            
            entry = tk.Entry(row, bg=BG_CARD, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER_COLOR, highlightcolor=ACCENT_TEAL)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=10, padx=(10, 0))
            entries[field_id] = entry
        
        iface_row = tk.Frame(form_frame, bg=BG_DARK)
        iface_row.pack(fill=tk.X, pady=8)
        
        tk.Label(iface_row, text="Interface", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10), width=12, anchor="w").pack(side=tk.LEFT)
        
        interface_names = []
        interface_map = {}
        for iface in self.interfaces:
            name = iface.get('name', 'Unknown')
            ipv4 = iface.get('ipv4', '')
            idx = iface.get('index', '')
            display = f"{name}" + (f" ({ipv4})" if ipv4 else "")
            interface_names.append(display)
            interface_map[display] = {'index': idx, 'ipv4': ipv4}
        
        interface_var = tk.StringVar()
        interface_combo = ttk.Combobox(iface_row, textvariable=interface_var, values=interface_names, state="readonly", font=("Segoe UI", 10))
        interface_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        
        def on_interface_selected(event=None):
            selected = interface_var.get()
            if selected in interface_map:
                iface_data = interface_map[selected]
                selected_interface_index.set(iface_data['index'])
                ipv4 = iface_data['ipv4']
                if ipv4:
                    entries["gateway"].delete(0, tk.END)
                    entries["gateway"].insert(0, ipv4)
        
        interface_combo.bind("<<ComboboxSelected>>", on_interface_selected)
        
        if interface_names:
            interface_combo.current(0)
            on_interface_selected()
        
        persistent_var = tk.BooleanVar(value=False)
        persist_frame = tk.Frame(form_frame, bg=BG_DARK)
        persist_frame.pack(fill=tk.X, pady=15)
        persist_check = ttk.Checkbutton(persist_frame, text="Make Persistent (survives reboot)", variable=persistent_var, style="Dark.TCheckbutton")
        persist_check.pack(anchor=tk.W)
        
        def do_add():
            dest = entries["dest"].get().strip()
            mask = entries["mask"].get().strip()
            gateway = entries["gateway"].get().strip()
            ifindex = selected_interface_index.get()
            persistent = persistent_var.get()
            
            if not dest or not validate_ipv4(dest):
                messagebox.showerror("Error", "Invalid destination IP")
                return
            if not mask or not validate_subnet_mask(mask):
                messagebox.showerror("Error", "Invalid subnet mask")
                return
            if not gateway or not validate_ipv4(gateway):
                messagebox.showerror("Error", "Invalid gateway IP")
                return
            
            if persistent:
                if not messagebox.askyesno("Confirm", "This route will persist across reboots. Continue?"):
                    return
            
            cmd = ["route", "-p", "add"] if persistent else ["route", "add"]
            cmd.extend([dest, "mask", mask, gateway])
            if ifindex:
                cmd.extend(["IF", ifindex])
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                log_command(' '.join(cmd), result.stdout, result.stderr, result.returncode == 0)
                
                if result.returncode == 0:
                    iface_name = interface_var.get().split(" (")[0] if interface_var.get() else "N/A"
                    route_record = {
                        'destination': dest, 'mask': mask, 'gateway': gateway,
                        'interface': iface_name, 'persistent': "Yes" if persistent else "No",
                        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    self.added_routes.append(route_record)
                    save_added_routes(self.added_routes)
                    messagebox.showinfo("Success", f"Route to {dest} added successfully")
                    dialog.destroy()
                    self.refresh_routes()
                else:
                    messagebox.showerror("Error", result.stderr or result.stdout or "Failed to add route")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        
        btn_frame = tk.Frame(dialog, bg=BG_DARK)
        btn_frame.pack(pady=25)
        
        SlateButton(btn_frame, "Add Route", command=do_add, style="filled", width=130, height=42).pack(side=tk.LEFT, padx=10)
        SlateButton(btn_frame, "Cancel", command=dialog.destroy, style="outline", width=100, height=42).pack(side=tk.LEFT, padx=10)
    
    def show_delete_route_dialog(self):
        if not self.is_admin:
            messagebox.showerror("Administrator Required", "Run as Administrator to delete routes.")
            return
        
        dialog = self.create_dialog("Delete Route", 420, 280)
        
        form_frame = tk.Frame(dialog, bg=BG_DARK)
        form_frame.pack(fill=tk.X, padx=40)
        
        row = tk.Frame(form_frame, bg=BG_DARK)
        row.pack(fill=tk.X, pady=8)
        
        tk.Label(row, text="Destination", bg=BG_DARK, fg=TEXT_GRAY, font=("Segoe UI", 10), width=12, anchor="w").pack(side=tk.LEFT)
        dest_entry = tk.Entry(row, bg=BG_CARD, fg=TEXT_WHITE, insertbackground=TEXT_WHITE, font=("Segoe UI", 11), relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER_COLOR, highlightcolor=ACCENT_TEAL)
        dest_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=10, padx=(10, 0))
        
        selection = self.routes_tree.selection()
        if selection:
            item = self.routes_tree.item(selection[0])
            values = item['values']
            if values:
                dest_entry.insert(0, values[0])
        
        def do_delete():
            dest = dest_entry.get().strip()
            if not dest or not validate_ipv4(dest):
                messagebox.showerror("Error", "Invalid destination IP")
                return
            
            if not messagebox.askyesno("Confirm", f"Delete route to {dest}?"):
                return
            
            try:
                result = subprocess.run(["route", "delete", dest], capture_output=True, text=True, timeout=30)
                log_command(f"route delete {dest}", result.stdout, result.stderr, result.returncode == 0)
                
                if result.returncode == 0:
                    messagebox.showinfo("Success", f"Route to {dest} deleted")
                    dialog.destroy()
                    self.refresh_routes()
                else:
                    messagebox.showerror("Error", result.stderr or result.stdout or "Failed to delete route")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        
        btn_frame = tk.Frame(dialog, bg=BG_DARK)
        btn_frame.pack(pady=30)
        
        SlateButton(btn_frame, "Delete", command=do_delete, style="filled", width=110, height=42).pack(side=tk.LEFT, padx=10)
        SlateButton(btn_frame, "Cancel", command=dialog.destroy, style="outline", width=100, height=42).pack(side=tk.LEFT, padx=10)
    
    def show_interfaces_dialog(self):
        dialog = self.create_dialog("Network Interfaces", 650, 450)
        
        tree_frame = tk.Frame(dialog, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=(0, 20))
        
        columns = ("index", "name", "state", "ipv4")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", style="Treeview")
        
        tree.heading("index", text="Index")
        tree.heading("name", text="Name")
        tree.heading("state", text="State")
        tree.heading("ipv4", text="IPv4 Address")
        
        tree.column("index", width=60, anchor=tk.CENTER)
        tree.column("name", width=200, anchor=tk.W)
        tree.column("state", width=100, anchor=tk.CENTER)
        tree.column("ipv4", width=150, anchor=tk.W)
        
        for iface in self.interfaces:
            tree.insert("", tk.END, values=(
                iface.get('index', ''),
                iface.get('name', ''),
                iface.get('state', ''),
                iface.get('ipv4', '')
            ))
        
        tree.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        
        def do_refresh():
            for item in tree.get_children():
                tree.delete(item)
            self.refresh_interfaces()
            for iface in self.interfaces:
                tree.insert("", tk.END, values=(
                    iface.get('index', ''),
                    iface.get('name', ''),
                    iface.get('state', ''),
                    iface.get('ipv4', '')
                ))
        
        btn_frame = tk.Frame(dialog, bg=BG_DARK)
        btn_frame.pack(pady=(0, 20))
        SlateButton(btn_frame, "Refresh", command=do_refresh, style="filled", width=100, height=38).pack()
    
    def show_history_dialog(self):
        dialog = self.create_dialog("Route History", 750, 450)
        
        tree_frame = tk.Frame(dialog, bg=BG_CARD, highlightbackground=BORDER_COLOR, highlightthickness=1)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=(0, 20))
        
        columns = ("destination", "mask", "gateway", "interface", "persistent", "timestamp")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", style="Treeview")
        
        tree.heading("destination", text="Destination")
        tree.heading("mask", text="Mask")
        tree.heading("gateway", text="Gateway")
        tree.heading("interface", text="Interface")
        tree.heading("persistent", text="Persistent")
        tree.heading("timestamp", text="Timestamp")
        
        tree.column("destination", width=120)
        tree.column("mask", width=120)
        tree.column("gateway", width=120)
        tree.column("interface", width=80)
        tree.column("persistent", width=70, anchor=tk.CENTER)
        tree.column("timestamp", width=140)
        
        for route in self.added_routes:
            tree.insert("", tk.END, values=(
                route.get('destination', ''),
                route.get('mask', ''),
                route.get('gateway', ''),
                route.get('interface', ''),
                route.get('persistent', ''),
                route.get('timestamp', '')
            ))
        
        tree.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        
        def clear_history():
            if messagebox.askyesno("Confirm", "Clear route history?"):
                self.added_routes = []
                save_added_routes([])
                for item in tree.get_children():
                    tree.delete(item)
        
        btn_frame = tk.Frame(dialog, bg=BG_DARK)
        btn_frame.pack(pady=(0, 20))
        SlateButton(btn_frame, "Clear History", command=clear_history, style="outline", width=120, height=38).pack()
    
    def refresh_all(self):
        self.refresh_interfaces()
        self.refresh_routes()
        if self.current_view == "console":
            self.refresh_serial_ports()
        elif self.current_view == "nic":
            self.refresh_nic_configs()
    
    def refresh_interfaces(self):
        self.interfaces = []
        interfaces = self.discover_interfaces_powershell()
        if not interfaces:
            interfaces = self.discover_interfaces_netsh()
        self.interfaces = interfaces
    
    def discover_interfaces_powershell(self) -> List[Dict]:
        interfaces = []
        try:
            ps_script = '''
            $interfaces = Get-NetIPInterface -AddressFamily IPv4 | Select-Object ifIndex, InterfaceAlias, ConnectionState
            $addresses = Get-NetIPAddress -AddressFamily IPv4 | Select-Object ifIndex, IPAddress
            
            $result = @()
            foreach ($iface in $interfaces) {
                $addr = $addresses | Where-Object { $_.ifIndex -eq $iface.ifIndex } | Select-Object -First 1
                $obj = [PSCustomObject]@{
                    Index = $iface.ifIndex
                    Name = $iface.InterfaceAlias
                    State = $iface.ConnectionState
                    IPv4 = if ($addr) { $addr.IPAddress } else { "" }
                }
                $result += $obj
            }
            $result | ConvertTo-Json -Compress
            '''
            
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                if isinstance(data, dict):
                    data = [data]
                
                for item in data:
                    interfaces.append({
                        'index': str(item.get('Index', '')),
                        'name': item.get('Name', 'Unknown'),
                        'state': item.get('State', ''),
                        'ipv4': item.get('IPv4', '')
                    })
        except Exception as e:
            log_command("PowerShell interface discovery", "", str(e), False)
        
        return interfaces
    
    def discover_interfaces_netsh(self) -> List[Dict]:
        interfaces = []
        try:
            result = subprocess.run(
                ["netsh", "interface", "ipv4", "show", "interfaces"],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                for line in lines[3:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        try:
                            idx = parts[0]
                            if idx.isdigit():
                                state = parts[3] if len(parts) > 3 else ""
                                name = ' '.join(parts[4:]) if len(parts) > 4 else parts[-1]
                                interfaces.append({
                                    'index': idx,
                                    'name': name,
                                    'state': state,
                                    'ipv4': ''
                                })
                        except Exception:
                            continue
        except Exception as e:
            log_command("netsh interface discovery", "", str(e), False)
        
        return interfaces
    
    def toggle_auto_refresh(self):
        self.auto_refresh_enabled = self.auto_refresh_var.get()
        if self.auto_refresh_enabled:
            self.start_auto_refresh()
        else:
            self.stop_auto_refresh()
    
    def start_auto_refresh(self):
        if self.auto_refresh_enabled and self.auto_refresh_job is None:
            self.auto_refresh_tick()
    
    def stop_auto_refresh(self):
        if self.auto_refresh_job is not None:
            self.root.after_cancel(self.auto_refresh_job)
            self.auto_refresh_job = None
    
    def auto_refresh_tick(self):
        if self.auto_refresh_enabled:
            self.refresh_routes()
            self.auto_refresh_job = self.root.after(self.auto_refresh_interval, self.auto_refresh_tick)
    
    def refresh_routes(self):
        try:
            result = subprocess.run(
                ["route", "print", "-4"],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0:
                routes = self.parse_route_print(result.stdout)
                persistent_routes = self.get_persistent_routes()
                
                self.all_routes_data = []
                for route in routes:
                    is_persistent = "Unknown"
                    dest = route.get('destination', '')
                    if dest in persistent_routes:
                        is_persistent = "Yes"
                    elif dest not in ['0.0.0.0', '127.0.0.0', '127.0.0.1', '224.0.0.0', '255.255.255.255']:
                        is_persistent = "No"
                    
                    self.all_routes_data.append({
                        'destination': route.get('destination', ''),
                        'netmask': route.get('netmask', ''),
                        'gateway': route.get('gateway', ''),
                        'interface': route.get('interface', ''),
                        'metric': route.get('metric', ''),
                        'persistent': is_persistent
                    })
                
                self.update_tab_counts()
                self.filter_routes()
        except Exception:
            pass
    
    def parse_route_print(self, output: str) -> List[Dict]:
        routes = []
        lines = output.split('\n')
        
        in_active_routes = False
        for line in lines:
            if 'Active Routes:' in line:
                in_active_routes = True
                continue
            if 'Persistent Routes:' in line:
                break
            if not in_active_routes:
                continue
            if 'Network Destination' in line:
                continue
            if '==' in line:
                continue
            
            line = line.strip()
            if not line:
                continue
            
            parts = line.split()
            if len(parts) >= 5:
                try:
                    if validate_ipv4(parts[0]) or parts[0] == '0.0.0.0':
                        routes.append({
                            'destination': parts[0],
                            'netmask': parts[1],
                            'gateway': parts[2],
                            'interface': parts[3],
                            'metric': parts[4] if len(parts) > 4 else ''
                        })
                except Exception:
                    continue
        
        return routes
    
    def get_persistent_routes(self) -> set:
        persistent = set()
        try:
            result = subprocess.run(
                ["route", "print", "-4"],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0:
                lines = result.stdout.split('\n')
                in_persistent = False
                for line in lines:
                    if 'Persistent Routes:' in line:
                        in_persistent = True
                        continue
                    if in_persistent:
                        if '==' in line:
                            continue
                        if 'None' in line:
                            break
                        parts = line.strip().split()
                        if len(parts) >= 1 and (validate_ipv4(parts[0]) or parts[0] == '0.0.0.0'):
                            persistent.add(parts[0])
        except Exception:
            pass
        
        return persistent


def main():
    root = tk.Tk()
    app = RouteManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
