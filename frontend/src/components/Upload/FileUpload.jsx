import React, { useState, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { uploadFile } from "../../services/api";
import toast from "react-hot-toast";
import { FiUploadCloud, FiFile, FiCheckCircle } from "react-icons/fi";
import AnomalyList from "./AnomalyList";
import "./FileUpload.css";

function FileUpload({ onUploadSuccess }) {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState(null);

  const onDrop = useCallback(
    async (acceptedFiles) => {
      if (acceptedFiles.length === 0) return;
      const file = acceptedFiles[0];

      setUploading(true);
      setProgress(0);
      setResult(null);

      try {
        const res = await uploadFile(file, setProgress);
        setResult(res.data);
        toast.success(`Loaded ${res.data.rows} rows successfully!`);
        onUploadSuccess(res.data);
      } catch (err) {
        toast.error(err.response?.data?.detail || "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [onUploadSuccess]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "text/csv": [".csv"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
      "application/vnd.ms-excel": [".xls"],
      "application/json": [".json"],
      "application/octet-stream": [".parquet"],
    },
    maxFiles: 1,
    disabled: uploading,
  });

  return (
    <div className="upload-page">
      <div className="upload-header">
        <h2>Upload Your Data</h2>
        <p>Supports CSV, Excel (.xlsx/.xls), JSON, and Parquet files</p>
      </div>

      <div
        {...getRootProps()}
        className={`dropzone ${isDragActive ? "active" : ""} ${uploading ? "disabled" : ""}`}
      >
        <input {...getInputProps()} />
        <FiUploadCloud size={48} className="dropzone-icon" />
        {isDragActive ? (
          <p>Drop the file here...</p>
        ) : (
          <>
            <p className="dropzone-title">
              Drag & drop your data file here
            </p>
            <p className="dropzone-subtitle">or click to browse</p>
          </>
        )}
      </div>

      {uploading && (
        <div className="upload-progress">
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${progress}%` }} />
          </div>
          <span>{progress}% uploading...</span>
        </div>
      )}

      {result && (
        <div className="upload-result">
          <div className="result-success">
            <FiCheckCircle size={20} />
            <div>
              <h4>{result.message}</h4>
              <p>Session ID: {result.session_id.slice(0, 8)}...</p>
            </div>
          </div>

          <div className="result-schema">
            <h4>
              <FiFile size={14} /> Detected Schema
            </h4>
            <div className="schema-columns">
              {result.columns.map((col) => (
                <div key={col} className="schema-col">
                  <span className="col-name">{col}</span>
                  <span className="col-type">{result.dtypes[col]}</span>
                </div>
              ))}
            </div>
          </div>

          <AnomalyList anomalies={result.anomalies} />
        </div>
      )}
    </div>
  );
}

export default FileUpload;
