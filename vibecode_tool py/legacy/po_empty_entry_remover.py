import os
import tkinter as tk
from tkinter import filedialog

def fix_first_entry_logic(lines):
    # 1. Tìm vị trí msgctxt đầu tiên (bắt đầu entry 1 sau Header)
    start_index = -1
    for i, line in enumerate(lines):
        if line.startswith('msgctxt'):
            start_index = i
            break
    
    if start_index == -1:
        return lines

    # 2. Tìm dòng msgid đầu tiên kể từ sau msgctxt
    msgid_index = -1
    for i in range(start_index, len(lines)):
        if lines[i].startswith('msgid'):
            msgid_index = i
            break
            
    if msgid_index == -1:
        return lines

    # 3. Bắt đầu thực hiện logic xóa của cậu
    # Xóa dòng msgid
    lines.pop(msgid_index)
    
    # Tiếp tục kiểm tra và xóa các dòng tiếp theo cho đến khi gặp msgstr
    while msgid_index < len(lines):
        current_line = lines[msgid_index]
        if current_line.startswith('msgstr'):
            lines.pop(msgid_index) # Xóa nốt dòng msgstr
            break # Dừng lại
        else:
            lines.pop(msgid_index) # Xóa dòng không phải msgstr (nội dung thừa)
            
    return lines

def main():
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(title="Chọn thư mục PO")
    if not folder: return

    for r, _, files in os.walk(folder):
        for f in files:
            if f.endswith(".po") and "- Copy" not in f:
                fp = os.path.join(r, f)
                try:
                    # Đọc file theo dạng list các dòng để dễ xử lý pop()
                    with open(fp, 'r', encoding='utf-8') as file:
                        lines = file.readlines()
                    
                    if not lines: continue
                    
                    new_lines = fix_first_entry_logic(lines)
                    
                    with open(fp, 'w', encoding='utf-8') as file:
                        file.writelines(new_lines)
                except Exception:
                    continue

if __name__ == "__main__":
    main()