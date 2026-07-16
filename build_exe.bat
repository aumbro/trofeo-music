@echo off
REM ── แพ็ก vibe_tray เป็นแอป Windows (System Tray) ──
REM ต้องมี: pip install pyinstaller
REM ใช้ --onedir (โฟลเดอร์) แทน --onefile: เชื่อถือได้กว่า, ไม่โดน antivirus ล็อกตอน self-extract
REM ผลลัพธ์: dist\vibe\vibe.exe  (แจกทั้งโฟลเดอร์ dist\vibe\ / zip ก็ได้)
REM หมายเหตุ: ยังต้องมีจอ Trofeo + WinUSB (จอ auto-bind อยู่แล้ว)

pyinstaller --onedir --noconsole --name vibe --noconfirm ^
  --add-binary "libusb-1.0.dll;." ^
  --collect-all winsdk ^
  --collect-all soundcard ^
  --collect-all pystray ^
  --collect-all cv2 ^
  vibe_tray.py

echo.
echo ===== เสร็จ! รันได้ที่ dist\vibe\vibe.exe =====
echo (ถ้าอยากเห็น log ตอนดีบัก เปลี่ยน --noconsole เป็น --console แล้ว build ใหม่)
pause
