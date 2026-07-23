import re
from pathlib import Path

def add_spaces_after_ellipsis_sandwiched(target_folder: str):
    # (?<=\w) ensures there is a letter/number BEFORE the "..."
    # (?=\w) ensures there is a letter/number AFTER the "..."
    # If there is already a space, (?=\w) fails, so it safely skips it.
    pattern = re.compile(r'(?<=\w)\.\.\.(?=\w)')
    folder_path = Path(target_folder)

    for file_path in folder_path.rglob('*.po'):
        if not file_path.is_file():
            continue
            
        # Ignore files with "-Copy" or "- Copy" in the name
        name_lower = file_path.name.lower()
        if "-copy" in name_lower or "- copy" in name_lower:
            continue

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            changed = False
            in_msgstr = False
            
            for i, line in enumerate(lines):
                # Turn off replacement if we hit an English string or context tag
                if line.startswith('msgid') or line.startswith('msgctxt'):
                    in_msgstr = False
                # Turn on replacement if we hit a Vietnamese translated string
                elif line.startswith('msgstr'):
                    in_msgstr = True
                    
                # Only modify lines if we are inside the msgstr section
                if in_msgstr:
                    new_line = pattern.sub(r'... ', line)
                    if new_line != line:
                        lines[i] = new_line
                        changed = True
                        
            # Only write back to the file if changes were actually made
            if changed:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
                print(f"Updated: {file_path.name}")
                
        except Exception as e:
            print(f"Could not process {file_path.name}: {e}")

# Example usage:
add_spaces_after_ellipsis_sandwiched(r'D:\Danganronpa1Viet\danganronpa-viet-hoa')