"""
po_mass_replace.py — Logic module for mass replacements in .po files.
Rules are loaded from mass_replace_rules.json.
"""

import os
import re
import json

RULES_FILE = "mass_replace_rules.json"

def load_rules():
    """Loads criteria from JSON or creates a default one if missing."""
    if not os.path.exists(RULES_FILE):
        default_rules = [
            {
                "label": "Example: Change Hope's Peak to Đỉnh Hy Vọng",
                "character": None,
                "scope": None,
                "whole_word": True,
                "replace": [["Hope's Peak", "Đỉnh Hy Vọng"]]
            }
        ]
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(default_rules, f, indent=4, ensure_ascii=False)
        return default_rules

    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"✗ Error loading JSON rules: {e}")
        return []

# The Toolkit reads this variable to build the checkboxes
CRITERIA = load_rules()

# ════════════════════════════════════════════════════════════════════
#  CORE REPLACEMENT LOGIC
# ════════════════════════════════════════════════════════════════════

def _apply_in_clt_block(text, find_str, repl_str, clt_id, whole_word=False):
    """Targeted replacement inside specific <CLT N> tags."""
    pattern = rf"(<CLT\s+{clt_id}>)(.*?)(<CLT>)"
    count = 0
    
    def replacer(match):
        nonlocal count
        prefix, content, suffix = match.groups()
        if whole_word:
            new_content, n = re.subn(rf"\b{re.escape(find_str)}\b", repl_str, content)
        else:
            new_content = content.replace(find_str, repl_str)
            n = content.count(find_str)
        
        count += n
        return f"{prefix}{new_content}{suffix}"
    
    return re.sub(pattern, replacer, text, flags=re.DOTALL), count

def _apply_replacements(text, rules_list):
    """Processes the msgstr through rules. Returns (new_text, total_changes, triggered_labels)."""
    total_changes = 0
    triggered_labels = set()

    for rule in rules_list:
        rule_changes = 0
        scope = rule.get("scope")
        is_whole = rule.get("whole_word", False)
        label = rule.get("label", "Unknown Rule")
        
        for find_str, repl_str in rule.get("replace", []):
            if scope and scope.startswith("clt:"):
                clt_id = scope.split(":")[1]
                text, n = _apply_in_clt_block(text, find_str, repl_str, clt_id, is_whole)
                rule_changes += n
            else:
                if is_whole:
                    text, n = re.subn(rf"\b{re.escape(find_str)}\b", repl_str, text)
                    rule_changes += n
                else:
                    n = text.count(find_str)
                    text = text.replace(find_str, repl_str)
                    rule_changes += n
        
        if rule_changes > 0:
            total_changes += rule_changes
            triggered_labels.add(label)

    return text, total_changes, triggered_labels

def process_po_file(filepath, active_criteria, log):
    """Parses and updates a single .po file. Returns list of change details."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    file_changes_details = []
    
    # Regex to find msgctxt and msgstr blocks
    pattern = re.compile(r'(msgctxt\s+"(.*?)"\s+msgid\s+".*?"\s+msgstr\s+")((?:[^"\\]|\\.)*?)"', re.DOTALL)

    def outer_replacer(match):
        header, context, msgstr = match.groups()
        
        relevant_rules = []
        for crit in active_criteria:
            char_tag = crit.get("character")
            if not char_tag or char_tag.upper() in context.upper():
                relevant_rules.append(crit)

        if not relevant_rules:
            return match.group(0)

        new_msgstr, n, labels = _apply_replacements(msgstr, relevant_rules)
        if n > 0:
            file_changes_details.append({
                "context": context,
                "old": msgstr,
                "new": new_msgstr,
                "count": n,
                "labels": labels
            })
            return f"{header}{new_msgstr}\""
        return match.group(0)

    updated = pattern.sub(outer_replacer, content)

    if file_changes_details:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated)
            
    return file_changes_details

def process_path(path, active_criteria, log):
    """Walks the folder and applies selected JSON criteria."""
    if not active_criteria:
        log("⚠ No criteria selected.", "warn")
        return 0, 0

    total_files_changed = 0
    total_replacements = 0

    files_to_process = []
    if os.path.isfile(path) and path.lower().endswith(".po"):
        if "- copy" not in path.lower():
            files_to_process.append(path)
    elif os.path.isdir(path):
        for root, _, filenames in os.walk(path):
            for f in filenames:
                if f.lower().endswith(".po") and "- copy" not in f.lower():
                    files_to_process.append(os.path.join(root, f))

    for fpath in files_to_process:
            change_list = process_po_file(fpath, active_criteria, log)
            
            if change_list:
                log(f"✓ Updated: {os.path.basename(fpath)}", "good")
                
                for item in change_list:
                    # Log context in Gold
                    log(f"  [{item['context']}]", "head")
                    
                    # Log sentences with custom color tags
                    log(f"    - Old: \"{item['old'].strip()}\"", "old_sent")
                    log(f"    + New: \"{item['new'].strip()}\"", "new_sent")
                    
                    total_replacements += item['count']
                
                total_files_changed += 1

    return total_files_changed, total_replacements