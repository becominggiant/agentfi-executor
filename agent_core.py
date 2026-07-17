from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
import os
from dotenv import load_dotenv

load_dotenv()

class AgentState(dict):
    messages: list

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)  # Configure your preferred model

def agent_node(state):
    messages = state["messages"]
    response = llm.invoke(messages)
    return {"messages": messages + [response]}

# Example tool - expand with web3.py calls
def get_onchain_data(query: str):
    return f"Simulated on-chain response for '{query}'. Integrate real Web3 queries here (balances, prices, etc.)."

workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.set_entry_point("agent")
workflow.add_edge("agent", END)

app = workflow.compile()

if __name__ == "__main__":
    result = app.invoke({"messages": [HumanMessage(content="Test: Suggest a simple on-chain action for profit exploration on testnet.")]} )
    print(result["messages"][-1].content)