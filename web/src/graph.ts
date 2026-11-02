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
  coordinator: { x: 500, y: 105 },
  linkedin: { x: 150, y: 320 },
  verifier: { x: 500, y: 320 },
  indeed: { x: 850, y: 320 },
  analyst: { x: 180, y: 545 },
  registrar: { x: 500, y: 545 },
  writer: { x: 820, y: 545 },
  boards: { x: 500, y: 770 },
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
    start: anchor("coordinator", "bottom", -55),
    segments: [
      { c1: point(400, 218), c2: point(250, 205), end: anchor("linkedin", "top", 25) },
    ],
    labelX: 305,
    labelY: 208,
  },
  {
    id: "coordinator-indeed",
    from: "coordinator",
    to: "indeed",
    start: anchor("coordinator", "bottom", 55),
    segments: [
      { c1: point(600, 218), c2: point(750, 205), end: anchor("indeed", "top", -25) },
    ],
    labelX: 695,
    labelY: 208,
  },
  {
    id: "coordinator-verifier",
    from: "coordinator",
    to: "verifier",
    start: anchor("coordinator", "bottom", 0),
    segments: [
      { c1: point(500, 208), c2: point(500, 217), end: anchor("verifier", "top", 0) },
    ],
    labelX: 555,
    labelY: 212,
    dashed: true,
  },
  {
    id: "coordinator-registrar",
    from: "coordinator",
    to: "registrar",
    start: anchor("coordinator", "left", 40),
    segments: [
      { c1: point(300, 175), c2: point(330, 400), end: anchor("registrar", "left", -70) },
    ],
    labelX: 322,
    labelY: 318,
    dashed: true,
  },
  {
    id: "coordinator-analyst",
    from: "coordinator",
    to: "analyst",
    start: anchor("coordinator", "bottom", -95),
    segments: [
      { c1: point(360, 300), c2: point(320, 420), end: anchor("analyst", "top", -60) },
    ],
    labelX: 352,
    labelY: 362,
    dashed: true,
  },
  {
    id: "coordinator-writer",
    from: "coordinator",
    to: "writer",
    start: anchor("coordinator", "bottom", 95),
    segments: [
      { c1: point(640, 300), c2: point(680, 420), end: anchor("writer", "top", -60) },
    ],
    labelX: 648,
    labelY: 350,
    dashed: true,
  },
  {
    id: "linkedin-registrar",
    from: "linkedin",
    to: "registrar",
    start: anchor("linkedin", "bottom", 25),
    segments: [
      { c1: point(230, 435), c2: point(330, 425), end: anchor("registrar", "top", -60) },
    ],
    labelX: 250,
    labelY: 422,
  },
  {
    id: "indeed-registrar",
    from: "indeed",
    to: "registrar",
    start: anchor("indeed", "bottom", -25),
    segments: [
      { c1: point(770, 435), c2: point(670, 425), end: anchor("registrar", "top", 60) },
    ],
    labelX: 700,
    labelY: 422,
  },
  {
    id: "verifier-analyst",
    from: "verifier",
    to: "analyst",
    start: anchor("verifier", "bottom", -70),
    segments: [
      { c1: point(390, 432), c2: point(300, 425), end: anchor("analyst", "top", 60) },
    ],
    labelX: 360,
    labelY: 437,
  },
  {
    id: "analyst-registrar",
    from: "analyst",
    to: "registrar",
    start: anchor("analyst", "right", -15),
    segments: [
      { c1: point(320, 530), c2: point(360, 530), end: anchor("registrar", "left", -15) },
    ],
    labelX: 340,
    labelY: 512,
  },
  {
    id: "registrar-writer",
    from: "registrar",
    to: "writer",
    start: anchor("registrar", "right", 0),
    segments: [
      { c1: point(640, 545), c2: point(680, 545), end: anchor("writer", "left", 0) },
    ],
    labelX: 660,
    labelY: 527,
    dashed: true,
  },
  {
    id: "coordinator-boards",
    from: "coordinator",
    to: "boards",
    start: anchor("coordinator", "left", 40),
    segments: [
      { c1: point(80, 220), c2: point(40, 600), end: anchor("boards", "left", -30) },
    ],
    labelX: 64,
    labelY: 470,
    dashed: true,
  },
  {
    id: "boards-registrar",
    from: "boards",
    to: "registrar",
    start: anchor("boards", "top", 0),
    segments: [
      { c1: point(500, 664), c2: point(500, 652), end: anchor("registrar", "bottom", 0) },
    ],
    labelX: 556,
    labelY: 656,
  },
];

export const EDGES: EdgeDef[] = ROUTES.map(makeEdge);

const EDGE_PAIRS: Record<string, { id: string; reverse: boolean }> = {
  "coordinator:linkedin": { id: "coordinator-linkedin", reverse: false },
  "linkedin:coordinator": { id: "coordinator-linkedin", reverse: true },
  "coordinator:indeed": { id: "coordinator-indeed", reverse: false },
  "indeed:coordinator": { id: "coordinator-indeed", reverse: true },
  "coordinator:verifier": { id: "coordinator-verifier", reverse: false },
  "verifier:coordinator": { id: "coordinator-verifier", reverse: true },
  "coordinator:registrar": { id: "coordinator-registrar", reverse: false },
  "registrar:coordinator": { id: "coordinator-registrar", reverse: true },
  "coordinator:analyst": { id: "coordinator-analyst", reverse: false },
  "analyst:coordinator": { id: "coordinator-analyst", reverse: true },
  "coordinator:writer": { id: "coordinator-writer", reverse: false },
  "writer:coordinator": { id: "coordinator-writer", reverse: true },
  "linkedin:registrar": { id: "linkedin-registrar", reverse: false },
  "registrar:linkedin": { id: "linkedin-registrar", reverse: true },
  "indeed:registrar": { id: "indeed-registrar", reverse: false },
  "registrar:indeed": { id: "indeed-registrar", reverse: true },
  "verifier:analyst": { id: "verifier-analyst", reverse: false },
  "analyst:verifier": { id: "verifier-analyst", reverse: true },
  "analyst:registrar": { id: "analyst-registrar", reverse: false },
  "registrar:analyst": { id: "analyst-registrar", reverse: true },
  "registrar:writer": { id: "registrar-writer", reverse: false },
  "writer:registrar": { id: "registrar-writer", reverse: true },
  "coordinator:boards": { id: "coordinator-boards", reverse: false },
  "boards:coordinator": { id: "coordinator-boards", reverse: true },
  "boards:registrar": { id: "boards-registrar", reverse: false },
  "registrar:boards": { id: "boards-registrar", reverse: true },
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
