import os
import sys
from groq import Groq

def run_review():
    api_key = os.environ.get("GROQ_API_KEY")
    diff_data = os.environ.get("PR_DIFF", "No diff available.")

    if not api_key:
        print("Error: GROQ_API_KEY is missing.")
        sys.exit(1)

    client = Groq(api_key=api_key)
    
    prompt = f"Analyze the following code diff for potential errors, bugs, or optimizations:\n\n{diff_data}"

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": "You are Project Hunter's automated PR reviewer. Provide concise, actionable remediation tips."},
            {"role": "user", "content": prompt}
        ]
    )

    review = response.choices[0].message.content
    print("=== HUNTER PR REVIEW RESULT ===")
    print(review)

    with open("review_comment.md", "w") as f:
        f.write(f"### 🎯 Project Hunter PR Remediation Analysis\n\n{review}")

if __name__ == "__main__":
    run_review()
