@echo off
rem Opens Chrome with a WAV file acting as the microphone, so the app can be
rem tested on a machine with no mic. Usage:
rem   dev-browser                        -> test_audio.wav (English)
rem   dev-browser test_audio_hinglish.wav -> pick Hinglish in the page
rem Needs the backend on :8000 and `npm run dev` on :5173 already running.

set WAV=%~dp0test_audio.wav
if not "%~1"=="" set WAV=%~dpnx1

start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --use-fake-device-for-media-stream ^
  --use-fake-ui-for-media-stream ^
  --use-file-for-fake-audio-capture="%WAV%" ^
  --user-data-dir="%TEMP%\chrome-fake-mic" ^
  --new-window ^
  http://localhost:5173
