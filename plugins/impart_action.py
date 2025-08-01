"""
KiCad Import Plugin for library files from various sources.
Supports Octopart, Samacsys, Ultralibrarian, Snapeda and EasyEDA.
"""

import os
import sys
import logging
from pathlib import Path
from time import sleep
from threading import Thread
from typing import Optional, List, Tuple, Any

# Setup paths for local imports
script_dir = Path(__file__).resolve().parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s [%(name)s:%(filename)s:%(lineno)d]: %(message)s",
    filename=script_dir / "plugin.log",
    filemode="w",
)

# Import dependencies
try:
    import wx

    logging.info("Successfully imported wx module")
except Exception as e:
    logging.exception("Failed to import wx module")
    raise

try:
    import impart_gui
    import FileHandler
    import KiCad_Settings
    import ConfigHandler
    import KiCadImport
    import KiCadSettingsPaths
    import impart_migration
    from single_instance_manager import SingleInstanceManager

    impartGUI = impart_gui.impartGUI
    FileHandler = FileHandler.FileHandler
    KiCad_Settings = KiCad_Settings.KiCad_Settings
    ConfigHandler = ConfigHandler.ConfigHandler
    LibImporter = KiCadImport.LibImporter
    KiCadApp = KiCadSettingsPaths.KiCadApp
    find_old_lib_files = impart_migration.find_old_lib_files
    convert_lib_list = impart_migration.convert_lib_list

    logging.info("Successfully imported all local modules")

except ImportError as e:
    logging.exception("Failed to import local modules")
    print(f"Import error: {e}")
    print(f"Python path: {sys.path}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Script directory: {script_dir}")
    raise

# Event handling
EVT_UPDATE_ID = wx.NewIdRef()


def EVT_UPDATE(win: wx.Window, func: Any) -> None:
    """Bind update event to window."""
    win.Connect(-1, -1, EVT_UPDATE_ID, func)


class ResultEvent(wx.PyEvent):
    """Custom event for thread communication."""

    def __init__(self, data: str) -> None:
        wx.PyEvent.__init__(self)
        self.SetEventType(EVT_UPDATE_ID)
        self.data = data


class PluginThread(Thread):
    """Background thread for monitoring import status."""

    def __init__(self, wx_object: wx.Window) -> None:
        Thread.__init__(self)
        self.wx_object = wx_object
        self.stop_thread = False
        self.start()

    def run(self) -> None:
        """Main thread loop."""
        len_str = 0
        while not self.stop_thread:
            current_len = len(backend_handler.print_buffer)
            if len_str != current_len:
                self.report(backend_handler.print_buffer)
                len_str = current_len
            sleep(0.5)

    def report(self, status: str) -> None:
        """Send status update to main thread."""
        wx.PostEvent(self.wx_object, ResultEvent(status))


class ImpartBackend:
    """Backend handler for the import plugin."""

    # Library names supported by the plugin
    SUPPORTED_LIBRARIES = [
        "Octopart",
        "Samacsys",
        "UltraLibrarian",
        "Snapeda",
        "EasyEDA",
    ]

    def __init__(self) -> None:
        """Initialize backend components."""
        logging.info("Initializing ImpartBackend")

        """Setup file paths."""
        self.config_path = os.path.join(os.path.dirname(__file__), "config.ini")

        """Initialize core components."""
        try:
            self.kicad_app = KiCadApp(prefer_ipc=True, min_version="8.0.4")
            self.config = ConfigHandler(self.config_path)
            self.kicad_settings = KiCad_Settings(self.kicad_app.settings_path)

            self.folder_handler = FileHandler(
                ".", min_size=1_000, max_size=50_000_000, file_extension=".zip"
            )

            self.importer = LibImporter()
            # Create a wrapper function that matches the expected signature
            self.importer.print = lambda txt: self.print_to_buffer(txt)

            logging.info("Successfully initialized all backend components")
            logging.info(f"KiCad settings path: {self.kicad_settings.SettingPath}")

        except Exception as e:
            logging.exception("Failed to initialize backend components")
            raise

        """Initialize control flags."""
        self.run_thread = False
        self.auto_import = False
        self.overwrite_import = False
        self.import_old_format = False
        self.local_lib = False
        self.auto_lib = False
        self.print_buffer = ""

        """Check initial configuration and version."""
        try:
            self.kicad_app.check_min_version(output_func=self.print_to_buffer)
        except Exception as e:
            logging.warning(f"Failed to check KiCad version: {e}")

        if not self.config.config_is_set:
            self._print_initial_warnings()

    def _print_initial_warnings(self) -> None:
        """Print initial configuration warnings."""
        warning_msg = (
            "Warning: The path where the libraries should be saved has not been "
            "adjusted yet. Maybe you use the plugin in this version for the first time."
        )

        info_msg = (
            "If this plugin is being used for the first time, settings in KiCad are "
            "required. The settings are checked at the end of the import process. "
            "For easy setup, auto setting can be activated."
        )

        self.print_to_buffer(warning_msg)
        self.print_to_buffer(info_msg)
        self.print_to_buffer("\n" + "=" * 50 + "\n")

    def print_to_buffer(self, *args: Any) -> None:
        """Add text to print buffer."""
        for text in args:
            self.print_buffer += str(text) + "\n"

    def find_and_import_new_files(self) -> None:
        """Monitor directory for new files and import them."""
        src_path = self.config.get_SRC_PATH()

        if not os.path.isdir(src_path):
            self.print_to_buffer(f"Source path does not exist: {src_path}")
            return

        while True:
            new_files = self.folder_handler.get_new_files(src_path)

            for lib_file in new_files:
                self._import_single_file(lib_file)

            if not self.run_thread:
                break
            sleep(1)

    def _import_single_file(self, lib_file: str) -> None:
        """Import a single library file."""
        try:
            # Convert string to Path for import_all function
            lib_path = Path(lib_file)
            result = self.importer.import_all(
                lib_path,
                overwrite_if_exists=self.overwrite_import,
                import_old_format=self.import_old_format,
            )
            # Handle potential None result
            if result and len(result) > 0:
                self.print_to_buffer(result[0])

        except AssertionError as e:
            self.print_to_buffer(f"Assertion Error: {e}")
        except Exception as e:
            error_msg = f"Import Error: {e}\nPython version: {sys.version}"
            self.print_to_buffer(error_msg)
            logging.exception("Import failed")
        finally:
            self.print_to_buffer("")


def check_library_import(backend: ImpartBackend, add_if_possible: bool = True) -> str:
    """Check and potentially add libraries to KiCad settings."""
    msg = ""

    if backend.local_lib:
        project_dir = backend.kicad_app.get_project_dir()
        if not project_dir:
            return "\nLocal library mode enabled but no KiCad project available."

        try:
            kicad_settings = KiCad_Settings(str(project_dir), path_prefix="${KIPRJMOD}")
            dest_path = project_dir
            logging.info("Project-specific library check completed")
        except Exception as e:
            logging.error(f"Failed to read project settings: {e}")
            return "\nCould not read project library settings."
    else:
        kicad_settings = backend.kicad_settings
        dest_path = backend.config.get_DEST_PATH()
        msg = kicad_settings.check_GlobalVar(dest_path, add_if_possible)

    for lib_name in ImpartBackend.SUPPORTED_LIBRARIES:
        msg += _check_single_library(
            kicad_settings, lib_name, dest_path, add_if_possible
        )

    return msg


def _check_single_library(
    kicad_settings: KiCad_Settings,
    lib_name: str,
    dest_path: str,
    add_if_possible: bool,
) -> str:
    """Check a single library for import."""
    msg = ""

    # Check for symbol libraries
    symbol_variants = [
        f"{lib_name}.kicad_sym",
        f"{lib_name}_kicad_sym.kicad_sym",
        f"{lib_name}_old_lib.kicad_sym",
    ]

    for variant in symbol_variants:
        if os.path.isfile(os.path.join(dest_path, variant)):
            msg += kicad_settings.check_symbollib(variant, add_if_possible)
            break

    # Check for footprint libraries
    if os.path.isdir(os.path.join(dest_path, f"{lib_name}.pretty")):
        msg += kicad_settings.check_footprintlib(lib_name, add_if_possible)

    return msg


instance_manager = SingleInstanceManager()  # Create global instance manager


class ImpartFrontend(impartGUI):
    """Frontend GUI with IPC-based singleton."""

    def __init__(self) -> None:
        super().__init__(None)

        # Register with instance manager
        if not instance_manager.register_frontend(self):
            # Another instance already exists - this shouldn't happen
            logging.warning("Frontend instance already exists - destroying this one")
            self.Destroy()
            return

        # Set window icon
        try:
            icon_path = Path(__file__).resolve().parent / "icon.png"
            if icon_path.exists():
                icon = wx.Icon(str(icon_path), wx.BITMAP_TYPE_PNG)
                self.SetIcon(icon)
        except Exception as e:
            logging.warning(f"Could not set window icon: {e}")

        self.backend = backend_handler
        self.thread: Optional[PluginThread] = None

        self._setup_gui()
        self._setup_events()
        self._start_monitoring_thread()
        self._print_initial_paths()

    def _setup_gui(self) -> None:
        """Initialize GUI components."""
        self.kicad_project = self.backend.kicad_app.get_project_dir()

        # Set initial values
        self.m_dirPicker_sourcepath.SetPath(self.backend.config.get_SRC_PATH())
        self.m_dirPicker_librarypath.SetPath(self.backend.config.get_DEST_PATH())

        # Set checkboxes
        self.m_autoImport.SetValue(self.backend.auto_import)
        self.m_overwrite.SetValue(self.backend.overwrite_import)
        self.m_check_autoLib.SetValue(self.backend.auto_lib)
        self.m_check_import_all.SetValue(self.backend.import_old_format)
        self.m_checkBoxLocalLib.SetValue(self.backend.local_lib)

        self._update_button_label()
        self._check_migration_possible()

    def _setup_events(self) -> None:
        """Setup event handlers."""
        EVT_UPDATE(self, self.update_display)

    def _start_monitoring_thread(self) -> None:
        """Start the monitoring thread."""
        self.thread = PluginThread(self)

    def _print_initial_paths(self) -> None:
        """Print initial source and destination paths."""
        src_path = self.backend.config.get_SRC_PATH()

        if self.backend.local_lib and self.kicad_project:
            dest_path = self.kicad_project
            lib_mode = "Local Project Library"
        else:
            dest_path = self.backend.config.get_DEST_PATH()
            lib_mode = "Global Library"

        self.backend.print_to_buffer(f"Library Mode: {lib_mode}")
        self.backend.print_to_buffer(f"Source Directory: {src_path}")
        self.backend.print_to_buffer(f"Destination Directory: {dest_path}")
        self.backend.print_to_buffer("=" * 50)

    def _print_path_change(self, change_type: str, new_value: str = "") -> None:
        """Print path change information."""
        if change_type == "library_mode":
            if self.backend.local_lib and self.kicad_project:
                dest_path = self.kicad_project
                lib_mode = "Local Project Library"
            else:
                dest_path = self.backend.config.get_DEST_PATH()
                lib_mode = "Global Library"

            self.backend.print_to_buffer(f"New Library Mode: {lib_mode}")
            self.backend.print_to_buffer(f"New Destination Directory: {dest_path}")
        elif change_type == "source":
            self.backend.print_to_buffer(f"New Source Directory: {new_value}")
        elif change_type == "destination":
            if not self.backend.local_lib:
                self.backend.print_to_buffer(f"New Destination Directory: {new_value}")

    def _update_button_label(self) -> None:
        """Update the main button label based on current state."""
        if self.backend.run_thread:
            self.m_button.Label = "automatic import / press to stop"
        else:
            self.m_button.Label = "Start"

    def update_display(self, status: ResultEvent) -> None:
        """Update the text display with new status."""
        self.m_text.SetValue(status.data)
        self.m_text.SetInsertionPointEnd()

    def m_checkBoxLocalLibOnCheckBox(self, event: wx.CommandEvent) -> None:
        """Handle local library checkbox change."""
        old_local_lib = self.backend.local_lib
        self.backend.local_lib = self.m_checkBoxLocalLib.IsChecked()
        self.m_dirPicker_librarypath.Enable(not self.backend.local_lib)

        # Print change information
        if old_local_lib != self.backend.local_lib:
            self._print_path_change("library_mode")

        event.Skip()

    def on_close(self, event: wx.CloseEvent) -> None:
        """Handle window close event."""
        if self.backend.run_thread:
            choice = self._confirm_background_process()
            if choice == "cancel":
                event.Veto()
                return
            elif choice == "background":
                self._save_settings()
                if not self.IsIconized():
                    self.Iconize(True)
                # self.Hide()
                logging.info("Frontend hidden - running in background with IPC active")
                event.Veto()
                return
            else:  # choice == "close"
                self.backend.run_thread = False
                self._save_settings()
                if self.thread:
                    self.thread.stop_thread = True
                instance_manager.stop_server()
                logging.info("Frontend and background process stopped")
                event.Skip()
        else:
            self._save_settings()
            if self.thread:
                self.thread.stop_thread = True
            instance_manager.stop_server()
            logging.info("Frontend instance closed and IPC server stopped")
            event.Skip()

    def _confirm_background_process(self) -> str:
        """Confirm what to do when background process is running."""
        msg = (
            "Import process runs in automatic mode.\n\n"
            "• HIDE: Keep running, hide window\n"
            "• STOP: Stop import and close\n"
            "• CANCEL: Back to window"
        )

        dlg = wx.MessageDialog(
            None,
            msg,
            "Import Running",
            wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION,
        )

        dlg.SetYesNoLabels("&Hide", "&Stop")

        result = dlg.ShowModal()
        dlg.Destroy()

        if result == wx.ID_YES:
            return "background"
        elif result == wx.ID_NO:
            return "close"
        else:
            return "cancel"

    def _save_settings(self) -> None:
        """Save current settings to backend."""
        self.backend.auto_import = self.m_autoImport.IsChecked()
        self.backend.overwrite_import = self.m_overwrite.IsChecked()
        self.backend.auto_lib = self.m_check_autoLib.IsChecked()
        self.backend.import_old_format = self.m_check_import_all.IsChecked()
        self.backend.local_lib = self.m_checkBoxLocalLib.IsChecked()

    def BottonClick(self, event: wx.CommandEvent) -> None:
        """Handle main button click."""
        self._update_backend_settings()

        if self.backend.run_thread:
            self._stop_import()
        else:
            self._start_import()

        event.Skip()

    def _update_backend_settings(self) -> None:
        """Update backend with current GUI settings."""
        if self.backend.local_lib:
            if not self.kicad_project:
                return
            self.backend.importer.set_DEST_PATH(Path(self.kicad_project))
            kicad_link = "${KIPRJMOD}"
        else:
            dest_path = self.backend.config.get_DEST_PATH()
            if dest_path:
                self.backend.importer.set_DEST_PATH(Path(dest_path))
            kicad_link = "${KICAD_3RD_PARTY}"

        self.backend.importer.KICAD_3RD_PARTY_LINK = kicad_link

        # Handle overwrite setting change
        overwrite_changed = (
            self.m_overwrite.IsChecked() and not self.backend.overwrite_import
        )
        if overwrite_changed:
            self.backend.folder_handler.known_files = set()

        self._save_settings()

    def _stop_import(self) -> None:
        """Stop the import process."""
        self.backend.run_thread = False
        self.m_button.Label = "Start"

    def _start_import(self) -> None:
        """Start the import process."""
        self.backend.run_thread = False
        self.backend.find_and_import_new_files()
        self.m_button.Label = "Start"

        if self.backend.auto_import:
            self.backend.run_thread = True
            self.m_button.Label = "automatic import / press to stop"

            import_thread = Thread(target=self.backend.find_and_import_new_files)
            import_thread.start()

        self._check_and_show_library_warnings()

    def _check_and_show_library_warnings(self) -> None:
        """Check library settings and show warnings if needed."""
        add_if_possible = self.m_check_autoLib.IsChecked()
        msg = check_library_import(self.backend, add_if_possible)

        if msg:
            self._show_library_warning(msg)

    def _show_library_warning(self, msg: str) -> None:
        """Show library configuration warning dialog."""
        full_msg = (
            f"{msg}\n\n"
            "More information can be found in the README for the integration into KiCad.\n"
            "github.com/Steffen-W/Import-LIB-KiCad-Plugin\n"
            "Some configurations require a KiCad restart to be detected correctly."
        )

        dlg = wx.MessageDialog(None, full_msg, "WARNING", wx.OK | wx.ICON_WARNING)

        if dlg.ShowModal() == wx.ID_OK:
            separator = "\n" + "=" * 50 + "\n"
            self.backend.print_to_buffer(separator + full_msg + separator)

    def DirChange(self, event: wx.CommandEvent) -> None:
        """Handle directory path changes."""
        # Get old values for comparison
        old_src = self.backend.config.get_SRC_PATH()
        old_dest = self.backend.config.get_DEST_PATH()

        # Update paths
        new_src = self.m_dirPicker_sourcepath.GetPath()
        new_dest = self.m_dirPicker_librarypath.GetPath()

        self.backend.config.set_SRC_PATH(new_src)
        self.backend.config.set_DEST_PATH(new_dest)
        self.backend.folder_handler.known_files = set()

        if old_src != new_src:
            self._print_path_change("source", new_src)
        if old_dest != new_dest:
            self._print_path_change("destination", new_dest)

        self._check_migration_possible()
        event.Skip()

    def ButtomManualImport(self, event: wx.CommandEvent) -> None:
        """Handle manual EasyEDA import."""
        try:
            self._perform_easyeda_import()
        except Exception as e:
            error_msg = f"Error: {e}\nPython version: {sys.version}"
            self.backend.print_to_buffer(error_msg)
            logging.exception("Manual import failed")
        finally:
            event.Skip()

    def _perform_easyeda_import(self) -> None:
        """Perform EasyEDA component import."""
        try:
            from impart_easyeda import import_easyeda_component, ImportConfig
        except ImportError as e:
            self.backend.print_to_buffer(f"Failed to import EasyEDA module: {e}")
            logging.error(f"EasyEDA import module not available: {e}")
            return

        if self.backend.local_lib:
            if not self.kicad_project:
                self.backend.print_to_buffer(
                    "Error: Local library mode selected, but no KiCad project is open."
                )
                self.backend.print_to_buffer("Please either:")
                self.backend.print_to_buffer("  1. Open a KiCad project first, or")
                self.backend.print_to_buffer(
                    "  2. Uncheck 'Local Library' to use global library path"
                )
                logging.error(
                    "Local library mode selected but no KiCad project available"
                )
                return

            # Verify the project path exists and is valid
            project_path = Path(self.kicad_project)
            if not project_path.exists() or not project_path.is_dir():
                self.backend.print_to_buffer(
                    f"Error: KiCad project directory does not exist: {self.kicad_project}"
                )
                self.backend.print_to_buffer("Please check your KiCad project setup.")
                logging.error(f"KiCad project directory invalid: {self.kicad_project}")
                return

            path_variable = "${KIPRJMOD}"
            base_folder = project_path
        else:
            path_variable = "${KICAD_3RD_PARTY}"
            base_folder = self.backend.config.get_DEST_PATH()

        config = ImportConfig(
            base_folder=Path(base_folder),
            lib_name="EasyEDA",
            overwrite=self.m_overwrite.IsChecked(),
            lib_var=path_variable,
        )

        component_id = self.m_textCtrl2.GetValue().strip()

        try:
            paths = import_easyeda_component(
                component_id=component_id,
                config=config,
                print_func=self.backend.print_to_buffer,
            )
            self.backend.print_to_buffer("")
            logging.info(f"Successfully imported EasyEDA component {component_id}")

        except ValueError as e:
            logging.error(f"Invalid component ID {component_id}: {e}")
        except RuntimeError as e:
            logging.error(f"Runtime error importing {component_id}: {e}")
        except Exception as e:
            self.backend.print_to_buffer(f"Unexpected error during import: {e}")
            logging.exception(f"Unexpected error importing {component_id}")

    def get_old_lib_files(self) -> dict:
        """Get list of old library files for migration."""
        lib_path = self.m_dirPicker_librarypath.GetPath()
        result = find_old_lib_files(
            folder_path=lib_path, libs=ImpartBackend.SUPPORTED_LIBRARIES
        )
        return result

    def _check_migration_possible(self) -> None:
        """Check if library migration is possible and show/hide button."""
        libs_to_migrate = self.get_old_lib_files()
        conversion_info = convert_lib_list(libs_to_migrate, drymode=True)

        if conversion_info:
            self.m_button_migrate.Show()
        else:
            self.m_button_migrate.Hide()

    def migrate_libs(self, event: wx.CommandEvent) -> None:
        """Handle library migration."""
        libs_to_migrate = self.get_old_lib_files()
        conversion_info = convert_lib_list(libs_to_migrate, drymode=True)

        if not conversion_info:
            self.backend.print_to_buffer("Error in migrate_libs()")
            return

        self._perform_migration(libs_to_migrate, conversion_info)
        self._check_migration_possible()
        event.Skip()

    def _perform_migration(
        self, libs_to_migrate: dict, conversion_info: List[Tuple]
    ) -> None:
        """Perform the actual library migration."""
        msg, lib_rename = self.backend.kicad_settings.prepare_library_migration(
            conversion_info
        )

        if not self._confirm_migration(msg):
            return

        self._execute_conversion(libs_to_migrate)

        if lib_rename:
            self._handle_library_renaming(msg, lib_rename)

    def _confirm_migration(self, msg: str) -> bool:
        """Confirm migration with user."""
        dlg = wx.MessageDialog(
            None, msg, "WARNING", wx.OK | wx.ICON_WARNING | wx.CANCEL
        )
        return dlg.ShowModal() == wx.ID_OK

    def _execute_conversion(self, libs_to_migrate: dict) -> None:
        """Execute the library conversion."""
        self.backend.print_to_buffer("Converted libraries:")
        conversion_results = convert_lib_list(libs_to_migrate, drymode=False)

        for old_path, new_path in conversion_results:
            if new_path.endswith(".blk"):
                self.backend.print_to_buffer(f"{old_path} rename to {new_path}")
            else:
                self.backend.print_to_buffer(f"{old_path} convert to {new_path}")

    def _handle_library_renaming(self, msg: str, lib_rename: List[dict]) -> None:
        """Handle library renaming in KiCad settings."""
        msg_lib = (
            "\nShould the change be made automatically? "
            "A restart of KiCad is then necessary to apply all changes."
        )

        dlg = wx.MessageDialog(
            None, msg + msg_lib, "WARNING", wx.OK | wx.ICON_WARNING | wx.CANCEL
        )

        if dlg.ShowModal() == wx.ID_OK:
            result_msg = self.backend.kicad_settings.execute_library_migration(
                lib_rename
            )
            self.backend.print_to_buffer(result_msg)
        else:
            self._show_manual_migration_instructions(lib_rename)

    def _show_manual_migration_instructions(self, lib_rename: List[dict]) -> None:
        """Show manual migration instructions."""
        if not lib_rename:
            return

        msg_summary = (
            "The following changes must be made to the list of imported Symbol libs:\n"
        )

        for item in lib_rename:
            msg_summary += f"\n{item['name']}: {item['oldURI']} \n-> {item['newURI']}"

        msg_summary += (
            "\n\nIt is necessary to adjust the settings of the imported "
            "symbol libraries in KiCad."
        )

        self.backend.print_to_buffer(msg_summary)


# Global backend instance
try:
    backend_handler = ImpartBackend()
    logging.info("Successfully created backend handler")
except Exception as e:
    logging.exception("Failed to create backend handler")
    raise

# KiCad Plugin Integration (SWIG)
# try:
#     import pcbnew

#     logging.info("Successfully imported pcbnew module")

#     class ActionImpartPlugin(pcbnew.ActionPlugin):
#         """KiCad Action Plugin for library import."""

#         def defaults(self) -> None:
#             """Set plugin defaults."""
#             plugin_dir = Path(__file__).resolve().parent
#             self.plugin_dir = plugin_dir

#             self.name = "impartGUI"
#             self.category = "Import library files"
#             self.description = "Import library files from Octopart, Samacsys, Ultralibrarian, Snapeda and EasyEDA"
#             self.show_toolbar_button = True

#             icon_path = plugin_dir / "icon.png"
#             logging.info(icon_path)
#             self.icon_file_name = str(icon_path)
#             self.dark_icon_file_name = str(icon_path)

#         def Run(self) -> None:
#             """Run the plugin."""
#             try:
#                 frontend = ImpartFrontend()
#                 frontend.ShowModal()
#                 frontend.Destroy()
#             except Exception as e:
#                 logging.exception("Failed to run plugin frontend")
#                 raise

# except ImportError:
#     logging.info("pcbnew module not available - running in standalone mode")

if __name__ == "__main__":
    logging.info("Starting application in standalone mode")

    if instance_manager.is_already_running():
        logging.info("Plugin already running - focus command sent")
        # Wait a bit for the command to be processed
        import time

        time.sleep(0.5)
        sys.exit(0)

    try:
        app = wx.App()
        frontend = ImpartFrontend()

        if not instance_manager.start_server(frontend):
            logging.warning("Failed to start IPC server - continuing anyway")

        frontend.ShowModal()
        frontend.Destroy()
        logging.info("Application finished successfully")

    except Exception as e:
        logging.exception("Failed to run standalone application")
        raise
    finally:
        instance_manager.stop_server()
