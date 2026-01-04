#!/usr/bin/env python3
"""
Sync Code Showcase - Fetches real code from repositories and updates README.

Extracts specific functions, structs, and symbols from source files and
embeds them in the profile README.
"""

import re
import urllib.request
import sys
from pathlib import Path

# Configuration: what to extract from where
CODE_SOURCES = [
    {
        "name": "Rust: VNC Server Library",
        "language": "rust",
        "repo": "rustvnc/rustvncserver",
        "branch": "main",
        "file": "src/lib.rs",
        "symbols": ["VncServer"],  # Will extract the VncServer struct/impl or module docs
        "extract_type": "module_docs",  # Extract the module-level documentation
    },
    {
        "name": "C: ESM Kernel Module",
        "language": "c",
        "repo": "esm-android/kernel-msm-esm",
        "branch": "esm",
        "file": "kernel/esm.c",
        "symbols": ["esm_device", "SYSCALL_DEFINE.*esm_wait"],
        "extract_type": "struct_and_syscall",
    },
    {
        "name": "C++: ECG T-Wave Analysis",
        "language": "cpp",
        "repo": "FDA/ecglib",
        "branch": "master",
        "file": "ecglib/src/delineators/twave/ecglib/delineator/twave/twaveDelineator.cpp",
        "symbols": ["twaveDelineator", "delineate"],
        "extract_type": "function",
        "fallback_file": "ecglib/src/delineators/twave/ecglib/delineator/twave/delineate.cpp",
        "fallback_symbols": ["delineate"],
    },
]

README_PATH = Path(__file__).parent.parent.parent / "README.md"


def fetch_file(repo: str, branch: str, file_path: str) -> str | None:
    """Fetch a file from GitHub raw content."""
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{file_path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read().decode("utf-8")
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")
        return None


def extract_c_struct(content: str, struct_name: str) -> str | None:
    """Extract a C struct definition."""
    # Match struct with name, capturing everything until closing brace and semicolon
    pattern = rf"(/\*[\s\S]*?\*/\s*)?(struct\s+{struct_name}\s*\{{[^}}]*\}})\s*;"
    match = re.search(pattern, content, re.MULTILINE)
    if match:
        comment = match.group(1) or ""
        struct = match.group(2)
        return (comment + struct + ";").strip()
    return None


def extract_c_syscall(content: str, pattern: str) -> str | None:
    """Extract a SYSCALL_DEFINE macro and its body."""
    # Find SYSCALL_DEFINE with the pattern
    syscall_pattern = rf"(/\*[\s\S]*?\*/\s*)?(SYSCALL_DEFINE\d*\s*\(\s*{pattern}[^)]*\))\s*(\{{)"
    match = re.search(syscall_pattern, content, re.MULTILINE)
    if not match:
        return None

    start = match.start()
    # Find the matching closing brace
    brace_count = 1
    pos = match.end()
    while pos < len(content) and brace_count > 0:
        if content[pos] == "{":
            brace_count += 1
        elif content[pos] == "}":
            brace_count -= 1
        pos += 1

    comment = match.group(1) or ""
    return (comment + content[match.start(2) : pos]).strip()


def extract_rust_module_docs(content: str) -> str | None:
    """Extract module-level documentation and key structures."""
    lines = content.split("\n")
    result_lines = []
    in_doc = False
    doc_started = False

    for line in lines:
        # Capture //! doc comments
        if line.strip().startswith("//!"):
            in_doc = True
            doc_started = True
            result_lines.append(line)
        elif doc_started and in_doc and not line.strip().startswith("//!"):
            # End of doc comments, get a few more lines of imports/structure
            in_doc = False
            break

    # Also get the public exports
    exports = []
    for line in lines:
        if line.startswith("pub use ") or line.startswith("pub mod "):
            exports.append(line)

    if result_lines:
        # Limit doc lines and add some exports
        doc_section = "\n".join(result_lines[:40])
        if exports:
            doc_section += "\n\n// Public API\n" + "\n".join(exports[:10])
        return doc_section

    return None


def extract_rust_struct(content: str, struct_name: str) -> str | None:
    """Extract a Rust struct or pub fn."""
    # Try struct first
    pattern = rf"(///[^\n]*\n)*\s*(pub\s+struct\s+{struct_name}\s*[^;]*\{{[^}}]*\}})"
    match = re.search(pattern, content)
    if match:
        return match.group(0).strip()

    # Try pub fn
    pattern = rf"(///[^\n]*\n)*\s*(pub\s+async\s+fn\s+{struct_name}|pub\s+fn\s+{struct_name})[^{{]*\{{"
    match = re.search(pattern, content)
    if match:
        start = match.start()
        brace_count = 1
        pos = match.end()
        while pos < len(content) and brace_count > 0:
            if content[pos] == "{":
                brace_count += 1
            elif content[pos] == "}":
                brace_count -= 1
            pos += 1
        return content[start:pos].strip()

    return None


def extract_cpp_function(content: str, func_name: str) -> str | None:
    """Extract a C++ function definition."""
    # Look for function with name
    pattern = rf"(/\*[\s\S]*?\*/\s*)?([\w\s\*&:<>]+\s+{func_name}\s*\([^)]*\))\s*\{{"
    match = re.search(pattern, content)
    if not match:
        # Try without return type complexity
        pattern = rf"(\w+\s+{func_name}\s*\([^)]*\))\s*\{{"
        match = re.search(pattern, content)
        if not match:
            return None

    start = match.start()
    brace_count = 1
    pos = match.end()
    while pos < len(content) and brace_count > 0:
        if content[pos] == "{":
            brace_count += 1
        elif content[pos] == "}":
            brace_count -= 1
        pos += 1

    result = content[start:pos].strip()
    # Limit length
    lines = result.split("\n")
    if len(lines) > 50:
        return "\n".join(lines[:50]) + "\n    // ... (truncated)"
    return result


def extract_cpp_header_and_code(content: str, max_lines: int = 50) -> str | None:
    """Extract file header comment and first meaningful code block."""
    lines = content.split("\n")
    result_lines = []
    in_header_comment = False
    header_done = False
    code_lines = 0

    for line in lines:
        # Capture file header comment block
        if not header_done:
            if line.strip().startswith("/**") or line.strip().startswith("/*"):
                in_header_comment = True
            if in_header_comment:
                result_lines.append(line)
                if "*/" in line:
                    in_header_comment = False
                    header_done = True
                continue

        # After header, get includes and first function/namespace
        if header_done:
            if line.strip().startswith("#include") or line.strip().startswith("namespace") or line.strip():
                result_lines.append(line)
                code_lines += 1
                if code_lines >= max_lines - len([l for l in result_lines if l.strip().startswith("*") or l.strip().startswith("/")]):
                    break

    if result_lines:
        return "\n".join(result_lines[:max_lines])
    return None


def extract_code(source: dict) -> str | None:
    """Extract code based on source configuration."""
    content = fetch_file(source["repo"], source["branch"], source["file"])

    # Try fallback if primary fails
    if not content and source.get("fallback_file"):
        print(f"Trying fallback file for {source['name']}")
        content = fetch_file(source["repo"], source["branch"], source["fallback_file"])
        if content:
            source["symbols"] = source.get("fallback_symbols", source["symbols"])

    if not content:
        return None

    extract_type = source.get("extract_type", "function")
    results = []

    if extract_type == "module_docs":
        result = extract_rust_module_docs(content)
        if result:
            return result

    for symbol in source["symbols"]:
        if source["language"] == "rust":
            result = extract_rust_struct(content, symbol)
        elif source["language"] == "c":
            if "SYSCALL" in symbol:
                result = extract_c_syscall(content, symbol.replace("SYSCALL_DEFINE.*", ""))
            else:
                result = extract_c_struct(content, symbol)
        elif source["language"] == "cpp":
            result = extract_cpp_function(content, symbol)
        else:
            result = None

        if result:
            results.append(result)

    # Fallback for C++: extract header and beginning of file if no symbols matched
    if not results and source["language"] == "cpp":
        result = extract_cpp_header_and_code(content)
        if result:
            results.append(result)

    return "\n\n".join(results) if results else None


def truncate_code(code: str, max_lines: int = 45) -> str:
    """Truncate code to max lines."""
    lines = code.split("\n")
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + "\n// ... (see full source)"
    return code


def generate_code_showcase() -> str:
    """Generate the code showcase section."""
    sections = []

    for source in CODE_SOURCES:
        print(f"Extracting from {source['repo']}...")
        code = extract_code(source)

        if code:
            code = truncate_code(code)
            lang = source["language"]
            repo_url = f"https://github.com/{source['repo']}/blob/{source['branch']}/{source['file']}"

            section = f"""<details>
<summary><b>{source['name']}</b> (click to expand)</summary>

<sub><a href="{repo_url}">View source</a></sub>

```{lang}
{code}
```
</details>"""
            sections.append(section)
        else:
            print(f"  Warning: Could not extract code for {source['name']}")
            # Add a fallback link-only version
            repo_url = f"https://github.com/{source['repo']}/blob/{source['branch']}/{source['file']}"
            section = f"""<details>
<summary><b>{source['name']}</b></summary>

<a href="{repo_url}">View source on GitHub</a>
</details>"""
            sections.append(section)

    return "\n\n".join(sections)


def update_readme(code_showcase: str) -> bool:
    """Update README.md with new code showcase."""
    if not README_PATH.exists():
        print(f"README not found at {README_PATH}")
        return False

    content = README_PATH.read_text()

    # Find and replace the code showcase section
    # Look for markers: <!-- CODE_SHOWCASE_START --> and <!-- CODE_SHOWCASE_END -->
    start_marker = "<!-- CODE_SHOWCASE_START -->"
    end_marker = "<!-- CODE_SHOWCASE_END -->"

    if start_marker in content and end_marker in content:
        before = content.split(start_marker)[0]
        after = content.split(end_marker)[1]
        new_content = f"{before}{start_marker}\n{code_showcase}\n{end_marker}{after}"
        README_PATH.write_text(new_content)
        print("README updated successfully!")
        return True
    else:
        print("Markers not found in README. Add these markers around your Code Showcase section:")
        print(f"  {start_marker}")
        print(f"  {end_marker}")
        return False


def main():
    print("Syncing code showcase from repositories...\n")

    code_showcase = generate_code_showcase()

    if "--dry-run" in sys.argv:
        print("\n--- Generated Code Showcase ---\n")
        print(code_showcase)
    else:
        update_readme(code_showcase)


if __name__ == "__main__":
    main()
