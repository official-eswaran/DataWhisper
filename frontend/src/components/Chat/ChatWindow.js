import React, { useState, useRef, useEffect } from "react";
import { askQuestionStream, exportPdf } from "../../services/api";
import toast from "react-hot-toast";
import { FiSend, FiDownload, FiCode, FiCpu, FiZap } from "react-icons/fi";
import ResultView from "../Visualization/ResultView";
import "./ChatWindow.css";

let _msgId = 0;
const nextId = () => `msg-${++_msgId}`;

// Human-readable labels for each pipeline stage
const STAGE_LABELS = {
  classifying: "Analyzing your question...",
  analyzing:   "Exploring your data structure...",
  generating:  "Crafting the SQL query...",
  executing:   "Running the query on your data...",
  healing:     "Fine-tuning the query...",
};

function ChatWindow({ session }) {
  const [messages, setMessages] = useState([
    {
      id: nextId(),
      role: "assistant",
      content: `Data loaded! Table "${session.table_name}" with ${session.rows} rows and ${session.columns?.length ?? 0} columns. Ask me anything about your data in plain English.`,
      type: "text",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [stageMessage, setStageMessage] = useState("");
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, stageMessage]);

  const handleSend = async () => {
    const question = input.trim();
    if (!question || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { id: nextId(), role: "user", content: question }]);
    setLoading(true);
    setStageMessage(STAGE_LABELS.classifying);

    try {
      await askQuestionStream(
        session.session_id,
        question,
        // onStage — update the live stage message
        (stage, message) => {
          setStageMessage(STAGE_LABELS[stage] || message);
        },
        // onDone — show the final result
        (result) => {
          setStageMessage("");
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              role: "assistant",
              content: result.summary,
              type: result.type,
              data: result.data,
              columns: result.columns,
              sql: result.sql,
              row_count: result.row_count,
            },
          ]);
        },
        // onError — show a friendly error message
        (errMsg) => {
          setStageMessage("");
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              role: "assistant",
              content: errMsg || "Something went wrong. Try rephrasing your question.",
              type: "error",
            },
          ]);
        }
      );
    } catch (err) {
      setStageMessage("");
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "assistant",
          content: err.message || "Connection error. Please try again.",
          type: "error",
        },
      ]);
    } finally {
      setLoading(false);
      setStageMessage("");
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleExport = async () => {
    try {
      const res = await exportPdf(session.session_id);
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `report_${session.table_name}.pdf`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast.success("Report downloaded!");
    } catch {
      toast.error("Failed to export report");
    }
  };

  const suggestions = [
    "Show me the total revenue",
    "What are the top 5 records?",
    "Show average by category",
    "Any trends over time?",
  ];

  return (
    <div className="chat-container">
      <div className="chat-header">
        <div>
          <h3>Ask Your Data</h3>
          <span className="chat-table-name">
            <FiCpu size={12} /> {session.table_name} — {session.rows} rows
          </span>
        </div>
        <button className="export-btn" onClick={handleExport}>
          <FiDownload size={14} />
          Export PDF
        </button>
      </div>

      <div className="chat-messages">
        {messages.map((msg) => (
          <div key={msg.id} className={`chat-msg ${msg.role}`}>
            {msg.role === "assistant" && (
              <div className="msg-avatar">
                <FiCpu size={16} />
              </div>
            )}
            <div className="msg-body">
              <p className="msg-text">{msg.content}</p>

              {msg.sql && (
                <details className="msg-sql">
                  <summary>
                    <FiCode size={12} /> View SQL Query
                  </summary>
                  <pre>{msg.sql}</pre>
                </details>
              )}

              {msg.data && msg.data.length > 0 && (
                <ResultView
                  type={msg.type}
                  data={msg.data}
                  columns={msg.columns}
                />
              )}
            </div>
          </div>
        ))}

        {/* Live stage indicator — shows what the AI is doing right now */}
        {loading && (
          <div className="chat-msg assistant">
            <div className="msg-avatar thinking">
              <FiZap size={16} />
            </div>
            <div className="msg-body">
              <div className="stage-indicator">
                <div className="typing-indicator">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
                {stageMessage && (
                  <span className="stage-text">{stageMessage}</span>
                )}
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {messages.length <= 1 && (
        <div className="chat-suggestions">
          {suggestions.map((s) => (
            <button key={s} className="suggestion-chip" onClick={() => setInput(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      <div className="chat-input-area">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a question about your data..."
          rows={1}
          disabled={loading}
        />
        <button className="send-btn" onClick={handleSend} disabled={loading || !input.trim()}>
          <FiSend size={18} />
        </button>
      </div>
    </div>
  );
}

export default ChatWindow;
