// Wire protocol + shared types. Mirrors backend/multiplayer/manager.py and physics/rules.py.
export type Slot = 'p1' | 'p2';
export type Difficulty = 'beginner' | 'intermediate' | 'grandmaster';
export type CoinType = 'queen' | 'white' | 'black' | 'striker';

export const BOARD = 740;
export const COIN_R = 15.1;
export const STRIKER_R = 18;
export const MAX_IMPULSE = 20;
export const BASELINE_Y: Record<Slot, number> = { p1: 620, p2: 120 };
export const BASELINE_MIN_X = 150;
export const BASELINE_MAX_X = 590;
export const POCKETS = [
  { id: 'p_top_left', x: 35, y: 35, radius: 30 },
  { id: 'p_top_right', x: 705, y: 35, radius: 30 },
  { id: 'p_bot_left', x: 35, y: 705, radius: 30 },
  { id: 'p_bot_right', x: 705, y: 705, radius: 30 },
];

export interface CoinSnap { id: string; type: CoinType; x: number; y: number; vx: number; vy: number; is_pocketed: boolean }
export interface PlayerSnap {
  id: string; name: string; assigned_color?: 'white' | 'black'; score?: number; fouls?: number;
  connected?: boolean; is_ai?: boolean; rating?: number;
}
export interface Snapshot {
  room_id: string; tick?: number; status: 'WAITING' | 'IN_PROGRESS' | 'FINISHED';
  board?: {
    dimensions: { width: number; height: number }; friction: number;
    pockets: { id: string; x: number; y: number; radius: number }[];
    coins: CoinSnap[];
    striker: { x: number; y: number; vx: number; vy: number; radius: number; is_in_hand: boolean };
  };
  players: Partial<Record<Slot, PlayerSnap>>;
  game_flow?: { current_turn: Slot; queen_covered: boolean; queen_pocketed_by: Slot | null; turn_time_remaining_sec: number };
  winner?: Slot | null;
}

export interface PhysEvent { tick: number; frame: number; type: 'striker_hit' | 'coin_hit' | 'border' | 'pocket'; a: string; b: string | null; intensity: number; x: number; y: number }

export interface AiDecision {
  agent_id: string; selected_strategy: string;
  rag_retrieval: { rule_reference: string | null; matching_historical_vector_id: string | null; similarity_score: number };
  shot_parameters: { striker_x: number; aim_angle_deg: number; force_percentage: number };
  thought_chain: string[]; difficulty: string; expected_value: number; simulations: number;
}

export interface TurnSummary {
  slot: Slot; pocketed: string[]; own_pocketed: number; opp_pocketed: number; queen_event: string | null;
  foul: boolean; foul_reason: string | null; score_delta: number; returned: string[]; next_turn: Slot;
  game_over: boolean; winner: Slot | null; ticks: number; final_hash: string;
}

export interface ShotPayload { striker_x: number; angle_rad: number; impulse_force: number; top_spin: number; side_spin: number }

export type ServerMessage =
  | { event: 'WELCOME'; room_id: string; slot: Slot | null; role: 'player' | 'spectator'; user_id: string; snapshot: Snapshot; turn_seconds: number }
  | { event: 'GAME_START' | 'STATE' | 'TURN_START' | 'TURN_TIMEOUT'; snapshot: Snapshot; slot?: Slot; turn_seconds?: number }
  | { event: 'PLAYER_STATUS'; slot: Slot | null; user_id: string; name?: string; connected: boolean; spectators?: number; grace_sec?: number }
  | { event: 'SHOT_START'; seq: number; slot: Slot; ticks: number; body_order: string[]; shot: ShotPayload & { striker_y: number }; ai: AiDecision | null }
  | { event: 'FRAMES'; seq: number; start: number; frames: number[][]; events: PhysEvent[] }
  | { event: 'TURN_RESULT'; seq: number; summary: TurnSummary; snapshot: Snapshot }
  | { event: 'GAME_OVER'; reason: string; winner: Slot | null; snapshot: Snapshot; ratings?: Record<string, number> }
  | { event: 'STRIKER_PREVIEW'; slot: Slot; x: number; angle_rad: number | null }
  | { event: 'CHAT'; from: string; slot: Slot | null; text: string }
  | { event: 'EMOJI'; from: string; slot: Slot | null; emoji: string }
  | { event: 'PONG'; t: number; server_time: number }
  | { event: 'KICKED'; reason: string }
  | { event: 'ERROR'; code: string; message: string };
