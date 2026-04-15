from ollama import chat

response = chat(
    model='qwen3.5:4b',
    messages=[{'role': 'user', 'content': 'Hello!'}],
)
print(response.message.content)