import os
import sys
import getpass
from dulwich.repo import Repo
from dulwich import porcelain

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def push():
    repo_path = os.path.dirname(os.path.abspath(__file__))
    repo = Repo(repo_path)
    remote_url = "https://github.com/thakurdaman/ai-google-review-generator.git"

    print("=" * 60)
    print("🚀 PUSHING TO GITHUB")
    print(f"Target: {remote_url}")
    print(f"Branch: main")
    print("=" * 60)

    # Check for GitHub token in env or prompt
    token = os.getenv("GITHUB_TOKEN", "").strip()
    username = os.getenv("GITHUB_USERNAME", "thakurdaman").strip()

    if not token:
        print("\nGitHub requires a Personal Access Token (PAT) to push over HTTPS.")
        print("You can generate one in 30 seconds at:")
        print("👉 https://github.com/settings/tokens (select 'repo' scope)\n")
        username = input(f"GitHub Username [{username}]: ").strip() or username
        token = getpass.getpass("Enter your GitHub Personal Access Token (PAT): ").strip()

    if not token:
        print("❌ Error: No token provided. Push aborted.")
        return

    # Authenticated URL
    auth_url = f"https://{username}:{token}@github.com/thakurdaman/ai-google-review-generator.git"

    print("\nPushing code to GitHub...")
    try:
        porcelain.push(repo, auth_url, refspecs=[b"refs/heads/main:refs/heads/main"])
        print("✅ SUCCESS! Code successfully pushed to https://github.com/thakurdaman/ai-google-review-generator")
    except Exception as e:
        print(f"❌ Push failed: {e}")
        print("\nAlternative: If you have Git installed, you can simply run:")
        print("  git push -u origin main")

if __name__ == "__main__":
    push()
