import type { AgentId } from "./types";

export interface NodePosition {
  x: number;
  y: number;
}

type NodeSide = "top" | "right" | "bottom" | "left";

interface Point {
  x: number;
  y: number;
}

interface CubicSegment {
  c1: Point;
  c2: Point;
  end: Point;
}

interface EdgeRoute {
  id: string;
  from: AgentId;
  to: AgentId;
  start: Point;
  segments: CubicSegment[];
  labelX: number;
  labelY: number;
  dashed?: boolean;
}

export interface EdgeDef {
  id: string;
  from: AgentId;
  to: AgentId;
  d: string;
  reverseD: string;
  labelX: number;
  labelY: number;
  dashed?: boolean;
}

export const CARD_WIDTH = 200;
export const CARD_HEIGHT = 184;

export const NODE_POSITIONS: Record<AgentId, NodePosition> = {
  coordinator: { x: 500, y: 130 },
  linkedin: { x: 180, y: 350 },
  indeed: { x: 820, y: 350 },
  registrar: { x: 320, y: 580 },
  writer: { x: 680, y: 580 },
};

function point(x: number, y: number): Point {
  return { x, y };
}

function anchor(id: AgentId, side: NodeSide, offset = 0): Point {
  const position = NODE_POSITIONS[id];
  const halfWidth = CARD_WIDTH / 2;
  const halfHeight = CARD_HEIGHT / 2;

  if (side === "top") return point(position.x + offset, position.y - halfHeight);
  if (side === "bottom") return point(position.x + offset, position.y + halfHeight);
  if (side === "left") return point(position.x - halfWidth, position.y + offset);
  return point(position.x + halfWidth, position.y + offset);
}

function command(value: number): string {
  return Number(value.toFixed(2)).toString();
}

function pathFrom(start: Point, segments: CubicSegment[]): string {
  let d = `M${command(start.x)} ${command(start.y)}`;
  for (const segment of segments) {
    d += ` C${command(segment.c1.x)} ${command(segment.c1.y)}`;
    d += ` ${command(segment.c2.x)} ${command(segment.c2.y)}`;
    d += ` ${command(segment.end.x)} ${command(segment.end.y)}`;
  }
  return d;
}

function reverseSegments(
  start: Point,
  segments: CubicSegment[],
): { start: Point; segments: CubicSegment[] } {
  const last = segments[segments.length - 1];
  const reversed: CubicSegment[] = [];

  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = segments[index];
    const previousEnd = index === 0 ? start : segments[index - 1].end;
    reversed.push({
      c1: segment.c2,
      c2: segment.c1,
      end: previousEnd,
    });
  }

  return { start: last.end, segments: reversed };
}

function makeEdge(route: EdgeRoute): EdgeDef {
  const reversed = reverseSegments(route.start, route.segments);
  return {
    id: route.id,
    from: route.from,
    to: route.to,
    d: pathFrom(route.start, route.segments),
    reverseD: pathFrom(reversed.start, reversed.segments),
    labelX: route.labelX,
    labelY: route.labelY,
    dashed: route.dashed,
  };
}

const ROUTES: EdgeRoute[] = [
  {
    id: "coordinator-linkedin",
    from: "coordinator",
    to: "linkedin",
    start: anchor("coordinator", "bottom", -60),
    segments: [
      { c1: point(440, 260), c2: point(230, 220), end: anchor("linkedin", "top", 10) },
    ],
    labelX: 310,
    labelY: 220,
  },
  {
    id: "coordinator-indeed",
    from: "coordinator",
    to: "indeed",
    start: anchor("coordinator", "bottom", 60),
    segments: [
      { c1: point(560, 260), c2: point(770, 220), end: anchor("indeed", "top", -10) },
    ],
    labelX: 690,
    labelY: 220,
  },
  {
    id: "coordinator-registrar",
    from: "coordinator",
    to: "registrar",
    start: anchor("coordinator", "bottom", -20),
    segments: [
      { c1: point(480, 340), c2: point(300, 370), end: anchor("registrar", "top", -20) },
    ],
    labelX: 410,
    labelY: 340,
    dashed: true,
  },
  {
    id: "coordinator-writer",
    from: "coordinator",
    to: "writer",
    start: anchor("coordinator", "bottom", 20),
    segments: [
      { c1: point(520, 340), c2: point(700, 370), end: anchor("writer", "top", 20) },
    ],
    labelX: 590,
    labelY: 340,
    dashed: true,
  },
  {
    id: "linkedin-registrar",
    from: "linkedin",
    to: "registrar",
    start: anchor("linkedin", "bottom", 0),
    segments: [
      { c1: point(180, 530), c2: point(190, 580), end: anchor("registrar", "left", 0) },
    ],
    labelX: 175,
    labelY: 535,
  },
  {
    id: "indeed-writer",
    from: "indeed",
    to: "writer",
    start: anchor("indeed", "bottom", 0),
    segments: [
      { c1: point(820, 530), c2: point(810, 580), end: anchor("writer", "right", 0) },
    ],
    labelX: 825,
    labelY: 535,
  },
  {
    id: "registrar-writer",
    from: "registrar",
    to: "writer",
    start: anchor("registrar", "right", 0),
    segments: [
      { c1: point(460, 580), c2: point(540, 580), end: anchor("writer", "left", 0) },
    ],
    labelX: 500,
    labelY: 580,
  },
  {
    id: "linkedin-indeed",
    from: "linkedin",
    to: "indeed",
    start: anchor("linkedin", "right", -25),
    segments: [
      { c1: point(420, 275), c2: point(580, 275), end: anchor("indeed", "left", -25) },
    ],
    labelX: 500,
    labelY: 265,
    dashed: true,
  },
  {
    id: "linkedin-writer",
    from: "linkedin",
    to: "writer",
    start: anchor("linkedin", "right", 25),
    segments: [
      { c1: point(410, 400), c2: point(560, 430), end: anchor("writer", "top", -35) },
    ],
    labelX: 470,
    labelY: 415,
    dashed: true,
  },
  {
    id: "indeed-registrar",
    from: "indeed",
    to: "registrar",
    start: anchor("indeed", "left", 25),
    segments: [
      { c1: point(590, 400), c2: point(440, 430), end: anchor("registrar", "top", 35) },
    ],
    labelX: 530,
    labelY: 415,
  },
];

export const EDGES: EdgeDef[] = ROUTES.map(makeEdge);

const EDGE_PAIRS: Record<string, { id: string; reverse: boolean }> = {
  "coordinator:linkedin": { id: "coordinator-linkedin", reverse: false },
  "linkedin:coordinator": { id: "coordinator-linkedin", reverse: true },
  "coordinator:indeed": { id: "coordinator-indeed", reverse: false },
  "indeed:coordinator": { id: "coordinator-indeed", reverse: true },
  "coordinator:registrar": { id: "coordinator-registrar", reverse: false },
  "registrar:coordinator": { id: "coordinator-registrar", reverse: true },
  "coordinator:writer": { id: "coordinator-writer", reverse: false },
  "writer:coordinator": { id: "coordinator-writer", reverse: true },
  "linkedin:registrar": { id: "linkedin-registrar", reverse: false },
  "registrar:linkedin": { id: "linkedin-registrar", reverse: true },
  "indeed:writer": { id: "indeed-writer", reverse: false },
  "writer:indeed": { id: "indeed-writer", reverse: true },
  "registrar:writer": { id: "registrar-writer", reverse: false },
  "writer:registrar": { id: "registrar-writer", reverse: true },
  "linkedin:indeed": { id: "linkedin-indeed", reverse: false },
  "indeed:linkedin": { id: "linkedin-indeed", reverse: true },
  "linkedin:writer": { id: "linkedin-writer", reverse: false },
  "writer:linkedin": { id: "linkedin-writer", reverse: true },
  "indeed:registrar": { id: "indeed-registrar", reverse: false },
  "registrar:indeed": { id: "indeed-registrar", reverse: true },
};

export function edgeFor(
  from: string,
  to: string,
): { edge: EdgeDef; path: string; reverse: boolean } | null {
  const match = EDGE_PAIRS[`${from}:${to}`];
  if (!match) return null;
  const edge = EDGES.find((item) => item.id === match.id);
  if (!edge) return null;
  return {
    edge,
    path: match.reverse ? edge.reverseD : edge.d,
    reverse: match.reverse,
  };
}
