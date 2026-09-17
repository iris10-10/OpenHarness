@echo off
set "ROOT=%CD%"
set "PYTHONPATH=%ROOT%\_baseline_tmp\src"
cd /d "%ROOT%\_baseline_tmp"
"%ROOT%\.venv\Scripts\python.exe" -c "import openharness, openharness.prompts.context as c; print('BASE pkg:', openharness.__file__); print('BASE ctx:', c.__file__)"
"%ROOT%\.venv\Scripts\python.exe" -m pytest tests/test_commands/test_registry.py tests/test_ui/test_modes.py tests/test_ui/test_react_backend.py tests/test_utils/test_shell.py tests/test_prompts/test_environment.py tests/test_tools/test_core_tools.py tests/test_sandbox/test_path_validator.py tests/test_hooks tests/test_tasks/test_manager.py tests/test_services/test_cron.py tests/test_services/test_cron_scheduler.py -q -p no:cacheprovider > "%ROOT%\_pytest_baseline.txt" 2>&1
echo EXIT=%errorlevel%
