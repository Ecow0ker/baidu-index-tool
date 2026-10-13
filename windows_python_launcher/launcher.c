#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <stdio.h>
#include <wchar.h>

#ifndef ENTRY_SCRIPT
#define ENTRY_SCRIPT L"run_qt_gui.py"
#endif
#ifndef EDITION_NAME
#define EDITION_NAME L"chromium"
#endif
#ifndef PAYLOAD_ID
#define PAYLOAD_ID L"development"
#endif

static int file_exists(const wchar_t *path) {
    DWORD attrs = GetFileAttributesW(path);
    return attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY);
}

static int read_marker(const wchar_t *path) {
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    wchar_t value[128] = {0};
    DWORD bytes = 0;
    BOOL ok = ReadFile(file, value, sizeof(value) - sizeof(wchar_t), &bytes, NULL);
    CloseHandle(file);
    return ok && wcscmp(value, PAYLOAD_ID) == 0;
}

static int write_marker(const wchar_t *path) {
    HANDLE file = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_HIDDEN, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    DWORD bytes = 0;
    BOOL ok = WriteFile(file, PAYLOAD_ID, (DWORD)(wcslen(PAYLOAD_ID) * sizeof(wchar_t)), &bytes, NULL);
    CloseHandle(file);
    return ok;
}

static int write_payload(const wchar_t *path) {
    HRSRC resource = FindResourceW(NULL, MAKEINTRESOURCEW(101), RT_RCDATA);
    if (!resource) return 0;
    HGLOBAL loaded = LoadResource(NULL, resource);
    if (!loaded) return 0;
    DWORD size = SizeofResource(NULL, resource);
    const void *data = LockResource(loaded);
    HANDLE file = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    DWORD written = 0;
    BOOL ok = WriteFile(file, data, size, &written, NULL);
    CloseHandle(file);
    return ok && written == size;
}

static int run_hidden_and_wait(wchar_t *command, const wchar_t *cwd) {
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_HIDE;
    if (!CreateProcessW(NULL, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, cwd, &startup, &process)) return 0;
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return code == 0;
}

static int run_gui_and_wait(wchar_t *command, const wchar_t *cwd) {
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    if (!CreateProcessW(NULL, command, NULL, NULL, FALSE, 0, NULL, cwd, &startup, &process)) return 0;
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return code == 0;
}

static int ensure_runtime(wchar_t *root, size_t root_count) {
    wchar_t local[MAX_PATH * 4] = {0};
    DWORD size = GetEnvironmentVariableW(L"LOCALAPPDATA", local, (DWORD)(sizeof(local) / sizeof(local[0])));
    if (!size || size >= sizeof(local) / sizeof(local[0])) {
        if (FAILED(SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, SHGFP_TYPE_CURRENT, local))) return 0;
    }
    _snwprintf(root, root_count, L"%ls\\CrossPlatformTools\\BaiduIndexTool\\2.4.3\\%ls", local, EDITION_NAME);
    SHCreateDirectoryExW(NULL, root, NULL);

    wchar_t marker[MAX_PATH * 4], python[MAX_PATH * 4];
    _snwprintf(marker, sizeof(marker) / sizeof(marker[0]), L"%ls\\.payload-id", root);
    _snwprintf(python, sizeof(python) / sizeof(python[0]), L"%ls\\pythonw.exe", root);
    if (file_exists(python) && read_marker(marker)) return 1;

    wchar_t zip_path[MAX_PATH * 4];
    _snwprintf(zip_path, sizeof(zip_path) / sizeof(zip_path[0]), L"%ls\\payload.zip", root);
    if (!write_payload(zip_path)) return 0;

    wchar_t command[32768];
    _snwprintf(command, sizeof(command) / sizeof(command[0]),
        L"powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"Get-ChildItem -LiteralPath '%ls' -Force | Where-Object {$_.Name -ne 'payload.zip'} | Remove-Item -Recurse -Force; Expand-Archive -LiteralPath '%ls' -DestinationPath '%ls' -Force\"",
        root, zip_path, root);
    if (!run_hidden_and_wait(command, root)) return 0;
    DeleteFileW(zip_path);
    return write_marker(marker) && file_exists(python);
}

static int run_python(const wchar_t *root, const wchar_t *arguments) {
    wchar_t python[MAX_PATH * 4], entry[MAX_PATH * 4], ca[MAX_PATH * 4], command[32768];
    _snwprintf(python, sizeof(python) / sizeof(python[0]), L"%ls\\pythonw.exe", root);
    _snwprintf(entry, sizeof(entry) / sizeof(entry[0]), L"%ls\\app\\%ls", root, ENTRY_SCRIPT);
    _snwprintf(ca, sizeof(ca) / sizeof(ca[0]), L"%ls\\Lib\\site-packages\\certifi\\cacert.pem", root);
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");
    SetEnvironmentVariableW(L"SSL_CERT_FILE", ca);
    _snwprintf(command, sizeof(command) / sizeof(command[0]), L"\"%ls\" \"%ls\" %ls", python, entry, arguments ? arguments : L"");
    return run_gui_and_wait(command, root);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show) {
    (void)instance; (void)previous; (void)show;
    wchar_t root[MAX_PATH * 4] = {0};
    if (!ensure_runtime(root, sizeof(root) / sizeof(root[0]))) {
        MessageBoxW(NULL, L"初始化 Python 运行环境失败。", L"百度指数查询工具", MB_ICONERROR);
        return 1;
    }
    if (!run_python(root, command_line)) {
        MessageBoxW(NULL, L"图形程序已退出，请重新启动。", L"百度指数查询工具", MB_ICONERROR);
        return 2;
    }
    return 0;
}
