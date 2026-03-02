import os
import requests
import base64
import json
import re
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import openai

load_dotenv()
NEBIUS_API_KEY = os.getenv("NEBIUS_API_KEY")

# --- CONFIGURATION ---

# Context limits
MAX_TOTAL_CONTEXT_CHARS = 120_000  # Total limit for LLM prompt
MAX_FILE_CHARS = 8_000             # Truncate individual files
MAX_FILES_TO_FETCH = 20            

# Smart Filtering
IGNORE_DIRS = {'.git', 'node_modules', '__pycache__', 'venv', 'env', 'dist', 'build', '.idea', '.vscode', 'target'}

# Prioritize source code over tests/docs
LOW_PRIORITY_DIRS = {'tests', 'test', 'docs', 'doc', 'examples', 'benchmarks'}

IGNORE_FILES = {'package-lock.json', 'yarn.lock', 'go.sum', 'cargo.lock', 'poetry.lock'}
IGNORE_EXT = {
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip', '.tar', '.gz', 
    '.pyc', '.lock', '.svg', '.ai', '.eps', '.mp4', '.mp3', '.bin', '.exe', '.dll'
}

app = Flask(__name__)

# Initialize Nebius Client (OpenAI SDK Compatible)
client = openai.OpenAI(
    base_url="https://api.studio.nebius.ai/v1/",
    api_key=NEBIUS_API_KEY
)

# --- HELPER CLASSES & EXCEPTIONS ---
class GitHubError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code

def get_headers():
    token = os.getenv("GITHUB_TOKEN")
    headers = {
        "User-Agent": "RepoSummarizer/4.0",
        "Accept": "application/vnd.github+json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def extract_owner_repo(github_url):
    """
    Parses various GitHub URL formats:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    """
    github_url = github_url.strip().rstrip("/")
    if github_url.endswith(".git"):
        github_url = github_url[:-4]
        
    pattern = r"github\.com/([^/]+)/([^/]+)"
    match = re.search(pattern, github_url)
    
    if not match:
        raise GitHubError("Invalid GitHub URL format.", 400)
    
    return match.group(1), match.group(2)

def fetch_github_api(url):
    """Generic wrapper to handle GitHub API limits and errors."""
    try:
        resp = requests.get(url, headers=get_headers(), timeout=10)
    except requests.exceptions.RequestException as e:
        raise GitHubError(f"Network error connecting to GitHub: {str(e)}", 502)

    if resp.status_code == 404:
        raise GitHubError("Repository or resource not found (Is it private?)", 404)
    if resp.status_code == 403:
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining and int(remaining) == 0:
            raise GitHubError("GitHub API rate limit exceeded. Please try again later.", 429)
        raise GitHubError("Access forbidden (403). Check your GITHUB_TOKEN.", 403)
    if resp.status_code >= 500:
        raise GitHubError("GitHub API server error.", 502)
        
    return resp.json()

# --- CORE LOGIC ---

def resolve_tree_sha(owner, repo):
    """
    Robust way to get the file tree:
    1. Get Repo Metadata -> Find default branch
    2. Get Branch Ref -> Find Commit SHA
    3. Get Commit -> Find Tree SHA
    """
    repo_json = fetch_github_api(f"https://api.github.com/repos/{owner}/{repo}")
    default_branch = repo_json.get("default_branch", "main")
    
    branch_json = fetch_github_api(f"https://api.github.com/repos/{owner}/{repo}/branches/{default_branch}")
    commit_sha = branch_json["commit"]["sha"]
    
    commit_json = fetch_github_api(f"https://api.github.com/repos/{owner}/{repo}/git/commits/{commit_sha}")
    tree_sha = commit_json["tree"]["sha"]
    
    return tree_sha

def get_file_priority(path):
    """
    Scoring system to select the most relevant files.
    Lower number = Higher priority.
    """
    p = path.lower()
    name = os.path.basename(p)
    
    # Tier 1: Key documentation & config
    if name in {'readme.md', 'readme.txt'}: return 0
    if name in {'pyproject.toml', 'setup.py', 'package.json', 'cargo.toml', 'go.mod', 'dockerfile', 'docker-compose.yml', 'makefile'}: return 1
    
    # Tier 2: Entry points and core logic
    if name in {'main.py', 'app.py', 'index.js', 'server.js', 'manage.py'}: return 2
    if p.startswith('src/') or p.startswith('app/') or p.startswith('lib/'): return 3
    
    # Tier 3: Logic deep in folders
    if '/' in p and not any(d in p for d in LOW_PRIORITY_DIRS): return 4
    
    # Tier 4: Tests and Docs (Included but prioritized last)
    if any(d in p for d in LOW_PRIORITY_DIRS): return 10
    
    return 5

def process_repository(github_url):
    owner, repo = extract_owner_repo(github_url)
    
    # 1. Resolve Tree SHA
    try:
        tree_sha = resolve_tree_sha(owner, repo)
    except Exception as e:
        if isinstance(e, GitHubError): raise e
        raise GitHubError(f"Failed to resolve repository tree: {str(e)}", 400)

    # 2. Fetch Tree
    tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1"
    tree_data = fetch_github_api(tree_url)
    
    # 3. Filter Files
    files = []
    for item in tree_data.get("tree", []):
        if item["type"] != "blob": continue
        
        path = item["path"]
        if any(ignored in path.split('/') for ignored in IGNORE_DIRS): continue
        if any(path.endswith(ext) for ext in IGNORE_EXT): continue
        if os.path.basename(path) in IGNORE_FILES: continue
        
        files.append(item)

    # 4. Sort by Priority
    files.sort(key=lambda x: get_file_priority(x['path']))
    
    # 5. Fetch Content
    context_parts = []
    current_chars = 0
    
    # Add a metadata header
    context_parts.append(f"Repository: {owner}/{repo}\nStructure Overview (Top files):")

    # Add top 25 file paths to give structure context even if we don't read them all
    for f in files[:25]:
        context_parts.append(f"- {f['path']}")
    context_parts.append("\nFile Contents:\n")

    files_processed = 0
    
    for item in files:
        if files_processed >= MAX_FILES_TO_FETCH: break
        if current_chars >= MAX_TOTAL_CONTEXT_CHARS: break
        
        try:
            # Note: We use raw requests here to avoid overhead, but rely on item['url'] which is the Blob API
            content_resp = requests.get(item["url"], headers=get_headers(), timeout=5)
            
            if content_resp.status_code == 200:
                data = content_resp.json()
                # GitHub Blob API returns base64
                raw_content = base64.b64decode(data['content']).decode('utf-8', errors='ignore')
                
                # Truncate large individual files
                if len(raw_content) > MAX_FILE_CHARS:
                    raw_content = raw_content[:MAX_FILE_CHARS] + "\n...[TRUNCATED]..."

                # XML-style tagging for clearer LLM parsing
                entry = f"\n<file path='{item['path']}'>\n{raw_content}\n</file>\n"
                
                if current_chars + len(entry) > MAX_TOTAL_CONTEXT_CHARS:
                    continue
                    
                context_parts.append(entry)
                current_chars += len(entry)
                files_processed += 1
                
        except Exception:
            continue # Skip file on error to prevent one bad file from breaking the request

    return "".join(context_parts)

def get_llm_summary(repo_context):
    prompt = f"""
    You are an expert technical auditor. Analyze the provided GitHub repository context and generate a JSON summary.
    
    Guidelines:
    - **summary**: 2-3 sentences explaining the project's purpose.
    - **technologies**: List languages, frameworks, and major third-party dependencies found in configuration files (like requirements.txt or setup.py) or imports.
    - **structure**: Describe the architecture based on folder names (e.g. "MVC pattern", "Monorepo").
    
    Input Context:
    {repo_context}
    
    Output JSON (Strict):
    {{
      "summary": "...",
      "technologies": ["...", "..."],
      "structure": "..."
    }}
    """

    try:
        response = client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct", # Ensure this model ID is valid in your Nebius dashboard
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1000,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"LLM Error: {e}")
        return None

# --- ROUTES ---

@app.route('/summarize', methods=['POST'])
def summarize():
    data = request.get_json()
    
    if not data or 'github_url' not in data:
        return jsonify({"status": "error", "message": "Missing 'github_url'"}), 400
    
    try:
        # 1. Fetch Code
        repo_context = process_repository(data['github_url'])
        
        # 2. LLM Analysis
        summary_json = get_llm_summary(repo_context)
        
        if not summary_json:
             return jsonify({
                "status": "error", 
                "message": "Failed to generate summary from LLM provider."
            }), 502

        # 3. Validate Output Schema
        required_keys = ["summary", "technologies", "structure"]
        if not all(k in summary_json for k in required_keys):
             # Fallback if LLM creates bad JSON
             return jsonify({
                 "summary": "Analysis incomplete.", 
                 "technologies": [], 
                 "structure": "Could not parse structure."
             }), 200

        return jsonify(summary_json)

    except GitHubError as e:
        return jsonify({"status": "error", "message": str(e)}), e.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": f"Internal Server Error: {str(e)}"}), 500

if __name__ == "__main__":
    if not NEBIUS_API_KEY:
        print("❌ CRITICAL: NEBIUS_API_KEY is missing from environment.")
        exit(1)
        
    app.run(host='0.0.0.0', port=8000, debug=False)