import React, { useState, useRef, useEffect, lazy, Suspense } from "react";
import { askQuestionStream, exportPdf } from "../../services/api";
import toast from "react-hot-toast";
import { FiSend, FiDownload, FiCode, FiCpu, FiZap } from "react-icons/fi";
import AnomalyList from "../Upload/AnomalyList";
import "./ChatWindow.css";

// Recharts is heavy; load the result/chart view only when a result exists so it
// stays out of the initial bundle (code-splitting).
const ResultView = lazy(() => import("../Visualization/ResultView"));

let _msgId = 0;
const nextId = () => `msg-${++_msgId}`;

const STAGE_LABELS = {
  classifying: "Analyzing your question...",
  analyzing:   "Exploring your data structure...",
  generating:  "Writing SQL query",       // token stream appends here
  executing:   "Running the query...",
  healing:     "Fine-tuning the query...",
};

function ChatWindow({ session }) {
  const [messages, setMessages]         = useState([
    {
      id: nextId(),
      role: "assistant",
      content: `Data loaded! Table "${session.table_name}" with ${session.rows} rows and ${session.columns?.length ?? 0} columns. Ask me anything about your data in plain English.`,
      type: "text",
      // What ingestion noticed about the file, carried on the greeting because
      // this is where a finished upload actually leaves the user. The upload
      // screen renders the same panel, but a successful upload switches to this
      // tab in the same commit, so nobody ever saw it there.
      anomalies: session.anomalies,
    },
  ]);
  const [input, setInput]               = useState("");
  const [loading, setLoading]           = useState(false);
  const [stageMessage, setStageMessage] = useState("");
  // live SQL tokens streaming in during generation
  const [streamingSQL, setStreamingSQL] = useState("");
  const bottomRef                       = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, stageMessage, streamingSQL]);

  const handleSend = async () => {
    const question = input.trim();
    if (!question || loading) return;

    setInput("");
    setStreamingSQL("");
    setMessages((prev) => [...prev, { id: nextId(), role: "user", content: question }]);
    setLoading(true);
    setStageMessage(STAGE_LABELS.classifying);

    try {
      await askQuestionStream(
        session.session_id,
        question,
        // onStage
        (stage) => {
          setStageMessage(STAGE_LABELS[stage] || "");
          if (stage !== "generating") setStreamingSQL("");
        },
        // onDone
        (result) => {
          setStageMessage("");
          setStreamingSQL("");
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              role: "assistant",
              // Error results arrive here too, not through onError — the stream
              // reports them as `stage: "done"` with an envelope that carries
              // `message` instead of `summary`. Reading only `summary` left the
              // bubble empty, so a failure that still produced SQL rendered as
              // nothing but a "View SQL Query" toggle.
              content:
                result.summary ??
                result.message ??
                "Something went wrong. Try rephrasing your question.",
              type: result.type,
              data: result.data,
              columns: result.columns,
              sql: result.sql,
              row_count: result.row_count,
            },
          ]);
        },
        // onError
        (errMsg) => {
          setStageMessage("");
          setStreamingSQL("");
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              role: "assistant",
              content: errMsg || "Something went wrong. Try rephrasing your question.",
              type: "error",
            },
          ]);
        },
        // onToken — append each SQL token as it arrives
        (token) => {
          setStreamingSQL((prev) => prev + token);
        },
      );
    } catch (err) {
      setStageMessage("");
      setStreamingSQL("");
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
      setStreamingSQL("");
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

  // Is the LLM currently streaming SQL tokens?
  const isStreamingSQL = loading && streamingSQL.length > 0;

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

              <AnomalyList anomalies={msg.anomalies} />

              {msg.sql && (
                <details className="msg-sql" open>
                  <summary>
                    <FiCode size={12} /> View SQL Query
                  </summary>
                  <pre>{msg.sql}</pre>
                </details>
              )}

              {msg.data && msg.data.length > 0 && (
                <Suspense fallback={<div className="chart-loading">Loading chart…</div>}>
                  <ResultView
                    type={msg.type}
                    data={msg.data}
                    columns={msg.columns}
                  />
                </Suspense>
              )}
            </div>
          </div>
        ))}

        {/* Live thinking/stage indicator */}
        {loading && (
          <div className="chat-msg assistant">
            <div className={`msg-avatar ${isStreamingSQL ? "" : "thinking"}`}>
              <FiZap size={16} />
            </div>
            <div className="msg-body">
              {isStreamingSQL ? (
                /* Token stream: show SQL being written in real-time */
                <div className="streaming-sql-wrapper">
                  <span className="streaming-label">
                    <FiCode size={11} /> Writing SQL
                    <span className="stream-cursor">▌</span>
                  </span>
                  <pre className="streaming-sql">{streamingSQL}</pre>
                </div>
              ) : (
                /* Stage label + typing dots when no tokens yet */
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
              )}
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
