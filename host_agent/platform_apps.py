"""Descoberta e abertura por APIs nativas, sem varredura global de executáveis."""
from dataclasses import replace
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

from .catalog import Entry, Inventory, fingerprint, stable_id


def aliases(name, executable=""):
    values = [name[:100]]
    stem = Path(executable).stem
    if stem and len(stem) > 2:
        values.append(stem[:100])
    # Aliases de apresentação, nunca usados para descobrir a existência do app.
    for prefix in ("Microsoft ", "Google ", "Mozilla "):
        if name.startswith(prefix):
            values.append(name[len(prefix):][:100])
    return tuple(dict.fromkeys(values))


def executable_entry(name, executable, arguments="", cwd="", source="menu", shortcut=None):
    exe = Path(os.path.expandvars(executable)).resolve(strict=True)
    if exe.suffix.lower() != ".exe" or not exe.is_file():
        raise ValueError("Atalho para documento, pasta ou script; não é um aplicativo executável")
    if arguments and exe.name.lower() in {"cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "mshta.exe"}:
        raise ValueError("Atalho contém script ou comando de interpretador")
    identity = os.path.normcase(str(exe))
    checks = [(str(exe), fingerprint(exe))]
    if shortcut:
        checks.append((str(shortcut), fingerprint(shortcut)))
    return Entry(stable_id(identity + "\0" + arguments + "\0" + cwd), name[:100],
                 "shortcut" if shortcut else "exe", str(shortcut or exe), identity,
                 str(exe), arguments, cwd, aliases(name, str(exe)), (source,), tuple(checks))


def _windows_browsers():
    """Metadados registrados pelos navegadores; não há lista de produtos."""
    import winreg
    found, default = {}, ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            default = winreg.QueryValueEx(key, "ProgId")[0]
    except OSError:
        pass
    import ctypes
    from ctypes import wintypes
    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(wintypes.LPWSTR)
    release = ctypes.windll.kernel32.LocalFree
    release.argtypes = [ctypes.c_void_p]
    release.restype = ctypes.c_void_p
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Clients\StartMenuInternet", 0, winreg.KEY_READ | view) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        name = winreg.EnumKey(root, index)
                        try:
                            with winreg.OpenKey(root, name + r"\shell\open\command") as key:
                                command = winreg.QueryValue(key, None)
                            count = ctypes.c_int()
                            values = parser(command, ctypes.byref(count))
                            if not values:
                                continue
                            try:
                                argv = [values[i] for i in range(count.value)]
                            finally:
                                release(values)
                            with winreg.OpenKey(root, name + r"\Capabilities\URLAssociations") as key:
                                progid = winreg.QueryValueEx(key, "https")[0]
                            display_name = name
                            try:
                                with winreg.OpenKey(root, name + r"\Capabilities") as key:
                                    registered_name = winreg.QueryValueEx(key, "ApplicationName")[0]
                                    if registered_name and not registered_name.startswith("@"):
                                        display_name = registered_name
                            except OSError:
                                pass
                            found[os.path.normcase(str(Path(argv[0]).resolve()))] = (progid == default, display_name)
                        except (OSError, IndexError):
                            continue
            except OSError:
                pass
    return found


def windows_scan():
    import pythoncom
    import win32com.client
    from win32com.shell import shell, shellcon
    inventory = Inventory()
    pythoncom.CoInitialize()
    try:
        wscript = win32com.client.Dispatch("WScript.Shell")
        for csidl, source, recursive in ((shellcon.CSIDL_DESKTOPDIRECTORY, "desktop", False),
                (shellcon.CSIDL_COMMON_DESKTOPDIRECTORY, "desktop", False),
                (shellcon.CSIDL_STARTMENU, "menu", True), (shellcon.CSIDL_COMMON_STARTMENU, "menu", True)):
            try:
                root = Path(shell.SHGetFolderPath(0, csidl, None, 0))
                files = root.rglob("*") if recursive else root.iterdir()
                for path in files:
                    if not path.is_file():
                        continue
                    if path.suffix.lower() != ".lnk":
                        if source == "desktop" or path.suffix.lower() == ".url":
                            inventory.exclude(path, "Não é um atalho de aplicativo")
                        continue
                    try:
                        link = wscript.CreateShortcut(str(path))
                        inventory.add(executable_entry(path.stem, link.TargetPath, link.Arguments,
                                                       link.WorkingDirectory, source, path))
                    except Exception as exc:
                        inventory.exclude(path, exc)
            except Exception as exc:
                inventory.exclude(f"KnownFolder:{csidl}", exc)
        shell_app = win32com.client.Dispatch("Shell.Application")
        folder = shell_app.NameSpace("shell:AppsFolder")
        for item in folder.Items():
            try:
                identifier = item.ExtendedProperty("System.AppUserModel.ID") or item.Path
                target = item.ExtendedProperty("System.Link.TargetParsingPath")
                if target and Path(target).suffix.lower() == ".exe":
                    inventory.add(executable_entry(item.Name, target))
                elif identifier and "!" in identifier:
                    inventory.add(Entry(stable_id("aumid:" + identifier), item.Name[:100], "aumid", identifier,
                                        "aumid:" + identifier, aliases=aliases(item.Name), sources=("menu",)))
                elif not any(item.Name in e.aliases for e in inventory.entries.values()):
                    inventory.exclude(item.Name, "Registro sem destino executável ou AUMID verificável")
            except Exception as exc:
                inventory.exclude(item.Name, exc)
        browsers = _windows_browsers()
        # Uma variante com perfil/incógnito não representa a associação padrão.
        for executable, (is_default, display_name) in browsers.items():
            if not any(e.identity == executable and not e.arguments for e in inventory.entries.values()):
                try:
                    inventory.add(executable_entry(display_name, executable))
                except (OSError, ValueError) as exc:
                    inventory.exclude(executable, exc)
        for key, entry in list(inventory.entries.items()):
            if entry.identity in browsers:
                inventory.entries[key] = replace(entry, browser=True, default=browsers[entry.identity][0] and not entry.arguments)
    finally:
        pythoncom.CoUninitialize()
    return inventory


def gio():
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    return Gio, GLib


def desktop_entry(info, source="menu", default_id=None):
    path = Path(info.get_filename()).resolve(strict=True)
    if not info.should_show() or info.get_boolean("Terminal"):
        raise ValueError("Entrada oculta, indisponível ou dependente de terminal")
    if info.get_string("Type") != "Application":
        raise ValueError("Entrada não representa um aplicativo")
    mime = info.get_supported_types() or []
    identity = "desktop:" + (info.get_id() or str(path))
    exe = shutil.which(info.get_executable() or "") or ""
    checks = [(str(path), fingerprint(path))]
    if exe:
        checks.append((exe, fingerprint(exe)))
    name = info.get_display_name() or info.get_name()
    return Entry(stable_id(identity + "\0" + (info.get_commandline() or "")), name[:100], "desktop", str(path),
        identity, exe, aliases=tuple(dict.fromkeys((*aliases(name, exe), *(str(k)[:100] for k in (info.get_keywords() or [])))))[:8],
        sources=(source,), checks=tuple(checks), browser="x-scheme-handler/https" in mime,
        default=info.get_id() == default_id)


def linux_scan():
    Gio, GLib = gio()
    inventory = Inventory()
    default = Gio.AppInfo.get_default_for_uri_scheme("https")
    default_id = default.get_id() if default else None
    # GIO aplica precedência XDG, Hidden/NoDisplay, Snap e Flatpak exportados.
    infos = [(info, "menu") for info in Gio.AppInfo.get_all() if hasattr(info, "get_filename")]
    desktop = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)
    if desktop and Path(desktop).is_dir():
        for path in Path(desktop).iterdir():
            if path.suffix != ".desktop" or not path.is_file():
                inventory.exclude(path, "Não é um lançador desktop de aplicativo")
                continue
            try:
                attributes = Gio.File.new_for_path(str(path)).query_info("metadata::trusted", Gio.FileQueryInfoFlags.NONE, None)
                trusted = attributes.get_attribute_string("metadata::trusted") == "true"
                if not trusted and not os.access(path, os.X_OK):
                    raise ValueError("Lançador do desktop ainda não autorizado pelo usuário")
                info = Gio.DesktopAppInfo.new_from_filename(str(path))
                if not info:
                    raise ValueError("Lançador inválido")
                infos.append((info, "desktop"))
            except Exception as exc:
                inventory.exclude(path, exc)
    for info, source in infos:
        try:
            inventory.add(desktop_entry(info, source, default_id))
        except Exception as exc:
            inventory.exclude(info.get_id() or info.get_name(), exc)
    return inventory


def bundle_entry(path, source="menu", alias=None, default_path=None):
    path = Path(path).resolve(strict=True)
    plist = path / "Contents/Info.plist"
    with plist.open("rb") as file:
        data = plistlib.load(file)
    if not isinstance(data, dict) or data.get("CFBundlePackageType") != "APPL" or not data.get("CFBundleIdentifier"):
        raise ValueError("Bundle não representa um aplicativo")
    if data.get("LSBackgroundOnly") or data.get("LSUIElement"):
        raise ValueError("Aplicativo auxiliar sem interface regular")
    executable = (path / "Contents/MacOS" / data["CFBundleExecutable"]).resolve(strict=True)
    if not executable.is_relative_to(path) or not executable.is_file():
        raise ValueError("Executável do bundle inválido")
    name = str(data.get("CFBundleDisplayName") or data.get("CFBundleName") or path.stem)
    schemes = [s for item in data.get("CFBundleURLTypes", []) for s in item.get("CFBundleURLSchemes", [])]
    identity = "bundle:" + str(path)
    checks = [(str(plist), fingerprint(plist)), (str(executable), fingerprint(executable))]
    if alias:
        checks.append((str(alias), fingerprint(alias)))
    return Entry(stable_id(identity), name[:100], "bundle", str(path), identity, str(executable),
                 aliases=tuple(dict.fromkeys((name[:100], path.stem[:100], *((Path(alias).stem[:100],) if alias else ())))),
                 sources=(source,), checks=tuple(checks), browser="https" in schemes,
                 default=str(path) == default_path)


def macos_scan():
    from AppKit import NSWorkspace
    from Foundation import NSURL
    inventory = Inventory()
    workspace = NSWorkspace.sharedWorkspace()
    default = workspace.URLForApplicationToOpenURL_(NSURL.URLWithString_("https://example.com"))
    default_path = str(Path(str(default.path())).resolve()) if default else None
    for root in (Path.home() / "Applications", Path("/Applications"), Path("/System/Applications")):
        if not root.is_dir():
            continue
        for directory, dirs, _ in os.walk(root, followlinks=False):
            bundles = [d for d in dirs if d.endswith(".app")]
            dirs[:] = [d for d in dirs if d not in bundles and not Path(directory, d).is_symlink()]
            for bundle in bundles:
                path = Path(directory, bundle)
                try:
                    inventory.add(bundle_entry(path, default_path=default_path))
                except Exception as exc:
                    inventory.exclude(path, exc)
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        for path in desktop.iterdir():
            try:
                target = path.resolve()
                alias = path if path.is_symlink() else None
                if not target.name.endswith(".app"):
                    url, error = NSURL.URLByResolvingAliasFileAtURL_options_error_(
                        NSURL.fileURLWithPath_(str(path)), (1 << 8) | (1 << 9), None)
                    if not url:
                        raise ValueError("Item não é um alias de aplicativo")
                    target, alias = Path(str(url.path())), path
                inventory.add(bundle_entry(target, "desktop", alias, default_path))
            except Exception as exc:
                inventory.exclude(path, exc)
    return inventory


def scan(platform):
    scanner = {"win32": windows_scan, "linux": linux_scan, "darwin": macos_scan}.get(platform)
    if not scanner:
        return Inventory(excluded=[{"source": platform, "reason": "Sistema não suportado"}])
    try:
        return scanner().distinguish()
    except Exception as exc:
        return Inventory(excluded=[{"source": platform, "reason": str(exc)}])


def launch(entry, url=None):
    entry.verify()
    if entry.kind in {"shortcut", "exe"}:
        import win32api
        if url:
            parameters = (entry.arguments + " " + subprocess.list2cmdline([url])).strip()
            win32api.ShellExecute(0, "open", entry.executable, parameters, entry.cwd or None, 1)
        else:
            os.startfile(entry.target, "open")
    elif entry.kind == "aumid":
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            folder = win32com.client.Dispatch("Shell.Application").NameSpace("shell:AppsFolder")
            item = folder.ParseName(entry.target)
            if not item or item.ExtendedProperty("System.AppUserModel.ID") != entry.target or item.Name not in (entry.name, *entry.aliases):
                raise ValueError("O registro do aplicativo mudou. Faça um novo pedido.")
        finally:
            pythoncom.CoUninitialize()
        # ShellExecute sobre item AppsFolder, sem compor comando de terminal.
        os.startfile("shell:AppsFolder\\" + entry.target, "open")
    elif entry.kind == "desktop":
        Gio, _ = gio()
        info = Gio.DesktopAppInfo.new_from_filename(entry.target)
        if not info:
            raise ValueError("Lançador removido")
        if url:
            info.launch_uris([url], None)
        else:
            info.launch([], None)
    elif entry.kind == "bundle":
        subprocess.Popen(["/usr/bin/open", "-a", entry.target, *([url] if url else [])], shell=False)
    else:
        raise ValueError("Lançador não suportado")
