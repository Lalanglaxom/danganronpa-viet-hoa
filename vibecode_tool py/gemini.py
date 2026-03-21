import os
import time
import re
from playwright.sync_api import sync_playwright

# ==========================================
# CONFIGURATION
# ==========================================
MAX_FILES_TO_TRANSLATE = 2  # Change this to limit how many files are processed per run
MAX_ENTRIES_PER_PROMPT = 60 # Maximum number of .po entries to send to Gemini at once
WAIT_TIME_BETWEEN_ASKS = 5  # Seconds to wait between sending prompts to Gemini
FOLDER_PATH = "D:\\Danganronpa1Viet\\translated_13-3-2026"           # Folder containing your .po files ('.' is current directory)
# ==========================================

def get_po_blocks(content):
    """Splits the PO file into blocks (separated by double newlines) and checks if translation is needed."""
    blocks = content.strip().split('\n\n')
    
    needs_translation = False
    # A simple check: if any block has an empty msgstr (and isn't the header), it needs translation.
    for block in blocks:
        if 'msgid "' in block and 'msgstr ""' in block and not block.startswith('msgid ""\nmsgstr ""'):
            needs_translation = True
            break
            
    return blocks, needs_translation

def chunk_blocks(blocks):
    """Groups blocks into chunks with a max of MAX_ENTRIES_PER_PROMPT entries."""
    chunks = []
    current_chunk = []
    entry_count = 0
    
    for block in blocks:
        current_chunk.append(block)
        if 'msgid "' in block:
            entry_count += 1
            
        if entry_count >= MAX_ENTRIES_PER_PROMPT:
            chunks.append('\n\n'.join(current_chunk))
            current_chunk = []
            entry_count = 0
            
    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))
        
    return chunks

def ask_gemini(page, prompt, is_summary=False):
    """Sends a prompt to Gemini and extracts the response."""
    chatbox = page.get_by_role("textbox")
    chatbox.click()
    chatbox.fill(prompt)
    page.keyboard.press("Enter")
    
    print("  -> Waiting for Gemini's response...")
    
    # Wait for the generation to start and finish
    stop_button_selector = 'button[aria-label*="Stop"], button[aria-label*="Dừng"]'
    try:
        page.wait_for_selector(stop_button_selector, state="visible", timeout=15000)
        page.wait_for_selector(stop_button_selector, state="detached", timeout=300000) # Wait up to 5 mins for long translations
    except:
        print("  -> Timeout waiting for 'Stop' button. Checking response anyway...")

    time.sleep(2) # Brief pause to let UI settle

    if is_summary:
        # Just grab the plain text response for the summary
        responses = page.locator('div.message-content, .model-response-text').all()
        if responses:
            return responses[-1].inner_text().strip()
        return "Translated"
    else:
        # Grab the code block for the translation
        last_code_block = page.locator("pre").last
        if last_code_block.is_visible():
            return last_code_block.inner_text().strip()
        else:
            # Fallback if Gemini forgets to use a code block
            responses = page.locator('div.message-content, .model-response-text').all()
            if responses:
                return responses[-1].inner_text().strip()
            return ""

def process_po_files():
    with sync_playwright() as p:
        try:
            print("Connecting to Chrome (make sure it's running with --remote-debugging-port=9222)...")
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            
            if "gemini.google.com" not in page.url:
                page.goto("https://gemini.google.com/app")
                page.wait_for_load_state("networkidle")

            po_files = [f for f in os.listdir(FOLDER_PATH) if f.endswith('.po') and '"' not in f]
            files_processed = 0

            for filename in po_files:
                if files_processed >= MAX_FILES_TO_TRANSLATE:
                    print(f"\nReached max limit of {MAX_FILES_TO_TRANSLATE} files. Stopping.")
                    break

                filepath = os.path.join(FOLDER_PATH, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()

                blocks, needs_translation = get_po_blocks(content)

                if not needs_translation:
                    print(f"\n[SKIPPING] {filename} (Already fully translated)")
                    continue

                print(f"\n[PROCESSING] {filename}")
                chunks = chunk_blocks(blocks)
                translated_content = ""

                # 1. Translate chunks
                for i, chunk in enumerate(chunks):
                    print(f"  -> Sending chunk {i+1}/{len(chunks)} ({chunk.count('msgid')} entries)...")
                    prompt = (
                        "Translate this .po file content into Vietnamese for a Danganronpa project. "
                        "Follow my 'Saved Info' for character tones. Only use one exclamation mark max. "
                        "Keep ellipses identical to the English source. Do not change the msgctxt or tags.\n"
                        "Return ONLY the exact translated .po content inside a code block.\n\n"
                        f"{chunk}"
                    )
                    
                    response_text = ask_gemini(page, prompt, is_summary=False)
                    
                    # Clean up markdown code block ticks if they exist
                    response_text = re.sub(r'^```(po|text)?\n', '', response_text)
                    response_text = re.sub(r'\n```$', '', response_text)
                    
                    translated_content += response_text + "\n\n"
                    
                    if i < len(chunks) - 1:
                        print(f"  -> Waiting {WAIT_TIME_BETWEEN_ASKS} seconds before next chunk...")
                        time.sleep(WAIT_TIME_BETWEEN_ASKS)

                # 2. Ask for a summary
                print("  -> Asking for a 2-3 word summary...")
                summary_prompt = "Based on the text we just translated, give me a short 2 to 3 word summary of the main event or topic. No quotes, no extra text, just the 2-3 words."
                summary = ask_gemini(page, summary_prompt, is_summary=True)
                
                # Clean up summary to be file-system safe
                safe_summary = re.sub(r'[\\/*?:"<>|]', "", summary).strip()
                if not safe_summary:
                    safe_summary = "Translated"

                # 3. Save and Rename
                base_name, ext = os.path.splitext(filename)
                new_filename = f'{base_name} "{safe_summary}"{ext}'
                new_filepath = os.path.join(FOLDER_PATH, new_filename)

                with open(new_filepath, 'w', encoding='utf-8') as f:
                    f.write(translated_content.strip() + "\n")

                print(f"  -> [SUCCESS] Saved as: {new_filename}")
                
                # Optionally, delete the old un-translated file:
                # os.remove(filepath) 
                
                files_processed += 1
                time.sleep(WAIT_TIME_BETWEEN_ASKS)

        except Exception as e:
            print(f"An error occurred: {e}")

if __name__ == "__main__":
    process_po_files()