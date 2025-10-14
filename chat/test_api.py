from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import HumanMessage

llm = ChatGoogleGenerativeAI(api_key=GOOGLE_API_KEY, model="gemini-2.5")
resp = llm.invoke([HumanMessage(content="Xin chào")])
print(resp.content)
