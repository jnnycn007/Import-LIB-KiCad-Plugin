"""
Single Instance Manager with IPC communication for KiCad Plugin.
Handles ensuring only one instance runs and brings existing window to foreground.
"""

import socket
import threading
import json
import logging
from typing import Optional, Any

try:
    import wx
except ImportError:
    wx = None


class SingleInstanceManager:
    """Manages single instance with IPC communication and window state."""

    def __init__(self, port: int = 59999):
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.server_thread: Optional[threading.Thread] = None
        self.running = False
        self.frontend_instance: Optional[Any] = None

    def is_already_running(self) -> bool:
        """Check if another instance is running and send focus command."""
        try:
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_socket.settimeout(2.0)  # Longer timeout
            client_socket.connect(("127.0.0.1", self.port))

            message = {"command": "focus"}
            data = json.dumps(message).encode("utf-8")
            client_socket.send(data)

            # Wait for response to ensure command was processed
            try:
                client_socket.settimeout(1.0)
                response = client_socket.recv(64)
                logging.debug(f"Received response: {response}")
            except socket.timeout:
                pass  # No response is OK

            client_socket.close()

            logging.info("Sent focus command to existing instance")
            return True

        except (socket.error, ConnectionRefusedError, OSError) as e:
            logging.debug(f"No existing instance found: {e}")
            return False

    def start_server(self, frontend_instance: Any) -> bool:
        """Start IPC server to listen for commands."""
        self.frontend_instance = frontend_instance

        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(("127.0.0.1", self.port))
            self.socket.listen(1)
            self.running = True

            self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
            self.server_thread.start()

            logging.info(f"IPC server started on port {self.port}")
            return True

        except socket.error as e:
            logging.error(f"Failed to start IPC server: {e}")
            return False

    def _server_loop(self) -> None:
        """Main server loop to handle incoming commands."""
        while self.running:
            try:
                self.socket.settimeout(1.0)  # Add timeout to server socket
                client_socket, addr = self.socket.accept()
                client_socket.settimeout(5.0)

                data = client_socket.recv(1024).decode("utf-8")
                if data:
                    try:
                        message = json.loads(data)
                        self._handle_command(message)
                        # Send acknowledgment
                        client_socket.send(b"OK")
                    except json.JSONDecodeError:
                        logging.warning("Received invalid JSON data")
                        client_socket.send(b"ERROR")

                client_socket.close()

            except socket.timeout:
                continue
            except socket.error as e:
                if self.running:
                    logging.error(f"Server socket error: {e}")
                break

    def _handle_command(self, message: dict) -> None:
        """Handle incoming commands."""
        command = message.get("command")

        if command == "focus" and self.frontend_instance:
            if wx:
                wx.CallAfter(self._bring_to_foreground)
                logging.info("Scheduled window focus command")
            else:
                logging.warning("wx not available - cannot bring window to foreground")

    def _bring_to_foreground(self) -> None:
        """Bring the window to foreground (must be called from main thread)."""
        if not self.frontend_instance:
            logging.warning("No frontend instance available")
            return

        try:
            # Check if window object is still valid
            if not hasattr(self.frontend_instance, "IsShown"):
                logging.error(
                    "Frontend instance has no IsShown method - window may be destroyed"
                )
                self.frontend_instance = None
                return

            # Handle hidden window (running in background)
            if not self.frontend_instance.IsShown():
                logging.info("Window is hidden - showing and bringing to foreground")
                self.frontend_instance.Show(True)

            # Handle iconized window
            if (
                hasattr(self.frontend_instance, "IsIconized")
                and self.frontend_instance.IsIconized()
            ):
                logging.info("Window is iconized - restoring")
                self.frontend_instance.Iconize(False)

            # Bring to foreground
            self.frontend_instance.Raise()
            self.frontend_instance.SetFocus()

            # Request user attention (platform-specific notification)
            if hasattr(self.frontend_instance, "RequestUserAttention"):
                self.frontend_instance.RequestUserAttention()

            logging.info("Successfully brought window to foreground")

        except Exception as e:
            logging.error(f"Failed to bring window to foreground: {e}")
            # If window is broken, reset the instance
            self.frontend_instance = None

    def register_frontend(self, frontend_instance: Any) -> bool:
        """Register a frontend instance. Returns True if this is the first instance."""
        if self.frontend_instance is None:
            self.frontend_instance = frontend_instance
            logging.info("Registered new frontend instance")
            return True
        else:
            logging.info(
                "Frontend instance already exists - new instance should not be created"
            )
            return False

    def unregister_frontend(self) -> None:
        """Unregister the current frontend instance."""
        self.frontend_instance = None
        logging.info("Unregistered frontend instance")

    def is_frontend_hidden(self) -> bool:
        """Check if frontend is currently hidden."""
        if self.frontend_instance and hasattr(self.frontend_instance, "IsShown"):
            return not self.frontend_instance.IsShown()
        return False

    def stop_server(self) -> None:
        """Stop the IPC server."""
        logging.info("Stopping IPC server")
        self.running = False

        if self.socket:
            try:
                self.socket.close()
            except Exception as e:
                logging.debug(f"Error closing socket: {e}")

        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=2.0)
            if self.server_thread.is_alive():
                logging.warning("Server thread did not stop cleanly")

        self.unregister_frontend()
        logging.info("IPC server stopped")
