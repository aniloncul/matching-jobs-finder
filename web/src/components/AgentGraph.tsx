import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { AGENT_ORDER } from "../agents";
import { EDGES, edgeFor } from "../graph";
import { useReducedMotion } from "../hooks/useReducedMotion";
import type { AgentId, AgentState, Signal } from "../types";
import { AgentNode } from "./AgentNode";

const BASE_WIDTH = 1000;
const BASE_HEIGHT = 700;

interface AgentGraphProps {
  agents: Record<string, AgentState>;
  signals: Record<string, Signal>;
  onAgentSelect: (id: AgentId) => void;
}

export function AgentGraph({
  agents,
  signals,
  onAgentSelect,
}: AgentGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const reducedMotion = useReducedMotion();
  const signalList = Object.values(signals);

  const updateScale = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const { clientWidth, clientHeight } = el;
    if (clientWidth <= 0 || clientHeight <= 0) return;

    const availableW = Math.max(0, clientWidth - 4);
    const availableH = Math.max(0, clientHeight - 4);
    const sX = availableW / BASE_WIDTH;
    const sY = availableH / BASE_HEIGHT;
    const fitted = Math.min(sX, sY);

    if (fitted > 0) {
      setScale(fitted);
    }
  }, []);

  useLayoutEffect(() => {
    updateScale();
    const el = containerRef.current;
    if (!el) return;

    const ro = new ResizeObserver(() => {
      updateScale();
    });
    ro.observe(el);

    window.addEventListener("resize", updateScale);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", updateScale);
    };
  }, [updateScale]);

  return (
    <div className="graph-viewport" ref={containerRef}>
      <div
        className="graph-stage-wrapper"
        style={{
          width: `${BASE_WIDTH * scale}px`,
          height: `${BASE_HEIGHT * scale}px`,
        }}
      >
        <div
          className="graph-stage"
          style={{
            width: `${BASE_WIDTH}px`,
            height: `${BASE_HEIGHT}px`,
            transform: `scale(${scale})`,
            transformOrigin: "0 0",
          }}
          role="group"
          aria-label="Agent communication topology"
        >
          <svg
            className="graph-svg"
            viewBox="0 0 1000 700"
            preserveAspectRatio="xMidYMid meet"
            aria-hidden="true"
          >
        <defs>
          <marker
            id="edge-arrow"
            markerWidth="8"
            markerHeight="8"
            refX="7"
            refY="4"
            orient="auto"
            markerUnits="strokeWidth"
          >
            <path d="M0 0L8 4L0 8Z" fill="var(--ink)" />
          </marker>
        </defs>

        {EDGES.map((edge) => (
          <path
            key={edge.id}
            d={edge.d}
            className={`graph-edge${edge.dashed ? " graph-edge--dashed" : ""}`}
            markerEnd="url(#edge-arrow)"
            vectorEffect="non-scaling-stroke"
          />
        ))}

        {signalList.map((signal) => {
          const resolved = edgeFor(signal.from, signal.to);
          if (!resolved) return null;
          const { edge, path } = resolved;
          const kindClass =
            signal.kind === "error" ? "graph-edge--error" : "graph-edge--live";

          return (
            <g key={`${signal.edgeKey}-${signal.seq}`}>
              <path
                d={path}
                className={`graph-edge graph-edge--active ${kindClass}`}
                markerEnd="url(#edge-arrow)"
                vectorEffect="non-scaling-stroke"
              />
              {reducedMotion ? (
                <rect
                  x={edge.labelX - 5}
                  y={edge.labelY - 5}
                  width="10"
                  height="10"
                  className={`signal-block${
                    signal.kind === "error" ? " signal-block--error" : ""
                  }`}
                />
              ) : (
                <rect
                  x="-5"
                  y="-5"
                  width="10"
                  height="10"
                  className={`signal-block${
                    signal.kind === "error" ? " signal-block--error" : ""
                  }`}
                >
                  <animateMotion
                    path={path}
                    dur="1.1s"
                    repeatCount="1"
                    fill="freeze"
                  />
                </rect>
              )}
            </g>
          );
        })}
      </svg>

      {AGENT_ORDER.map((id) => {
        const agent = agents[id];
        return agent ? (
          <AgentNode key={id} agent={agent} onOpen={() => onAgentSelect(id)} />
        ) : null;
      })}

      {signalList.map((signal) => {
        const resolved = edgeFor(signal.from, signal.to);
        if (!resolved) return null;
        return (
          <div
            key={`label-${signal.edgeKey}-${signal.seq}`}
            className={`edge-label edge-label--${signal.kind}`}
            style={{
              left: `${resolved.edge.labelX / 10}%`,
              top: `${resolved.edge.labelY / 7}%`,
            }}
            title={signal.text}
          >
            {signal.text}
          </div>
        );
      })}
        </div>
      </div>
    </div>
  );
}
