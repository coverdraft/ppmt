/**
 * TrieNodeMock — The contract between D3.js frontend and Python backend.
 *
 * This interface defines EXACTLY how the backend must serialize the Trie
 * for the frontend Trie Lab visualization. Any field added here must be
 * produced by the Python serializer in the future.
 */
export interface TrieNodeMock {
  id: string;               // e.g. "root" or "a-b-c-d-e"
  symbol: string | null;    // The SAX symbol of this branch (null for root)
  historical_count: number; // Drives LINK THICKNESS and NODE RADIUS
  confidence: number;       // 0.0–1.0 drives NODE COLOR (red→yellow→green)
  children?: TrieNodeMock[];
  is_active_path?: boolean; // If true, this node glows/pulses
  win_rate?: number;        // Optional — shown in tooltip
}

/**
 * Mock Trie data — 4 levels deep, simulating a PPMT N1–N4 structure.
 *
 * The active path (root → a → a-b → a-b-c → a-b-c-d) represents the
 * current prediction walk of the motor, which must glow in the UI.
 */
export const mockTrieData: TrieNodeMock = {
  id: "root",
  symbol: null,
  historical_count: 1000,
  confidence: 0.8,
  win_rate: 58,
  children: [
    {
      id: "a",
      symbol: "a",
      historical_count: 300,
      confidence: 0.6,
      win_rate: 55,
      is_active_path: true,
      children: [
        {
          id: "a-b",
          symbol: "b",
          historical_count: 150,
          confidence: 0.75,
          win_rate: 62,
          is_active_path: true,
          children: [
            {
              id: "a-b-c",
              symbol: "c",
              historical_count: 80,
              confidence: 0.85,
              win_rate: 68,
              is_active_path: true,
              children: [
                {
                  id: "a-b-c-d",
                  symbol: "d",
                  historical_count: 45,
                  confidence: 0.92,
                  win_rate: 73,
                  is_active_path: true,
                },
                {
                  id: "a-b-c-e",
                  symbol: "e",
                  historical_count: 25,
                  confidence: 0.55,
                  win_rate: 50,
                },
              ],
            },
            {
              id: "a-b-d",
              symbol: "d",
              historical_count: 50,
              confidence: 0.45,
              win_rate: 48,
            },
          ],
        },
        {
          id: "a-c",
          symbol: "c",
          historical_count: 100,
          confidence: 0.4,
          win_rate: 42,
          children: [
            {
              id: "a-c-a",
              symbol: "a",
              historical_count: 40,
              confidence: 0.35,
              win_rate: 38,
            },
            {
              id: "a-c-b",
              symbol: "b",
              historical_count: 45,
              confidence: 0.5,
              win_rate: 52,
            },
          ],
        },
        {
          id: "a-d",
          symbol: "d",
          historical_count: 30,
          confidence: 0.3,
          win_rate: 35,
        },
      ],
    },
    {
      id: "b",
      symbol: "b",
      historical_count: 400,
      confidence: 0.9,
      win_rate: 65,
      children: [
        {
          id: "b-a",
          symbol: "a",
          historical_count: 200,
          confidence: 0.85,
          win_rate: 63,
          children: [
            {
              id: "b-a-b",
              symbol: "b",
              historical_count: 120,
              confidence: 0.78,
              win_rate: 60,
            },
            {
              id: "b-a-c",
              symbol: "c",
              historical_count: 60,
              confidence: 0.7,
              win_rate: 57,
            },
          ],
        },
        {
          id: "b-c",
          symbol: "c",
          historical_count: 150,
          confidence: 0.65,
          win_rate: 54,
          children: [
            {
              id: "b-c-a",
              symbol: "a",
              historical_count: 70,
              confidence: 0.6,
              win_rate: 51,
            },
            {
              id: "b-c-d",
              symbol: "d",
              historical_count: 60,
              confidence: 0.55,
              win_rate: 49,
            },
          ],
        },
      ],
    },
    {
      id: "c",
      symbol: "c",
      historical_count: 200,
      confidence: 0.5,
      win_rate: 50,
      children: [
        {
          id: "c-a",
          symbol: "a",
          historical_count: 80,
          confidence: 0.42,
          win_rate: 44,
          children: [
            {
              id: "c-a-b",
              symbol: "b",
              historical_count: 35,
              confidence: 0.38,
              win_rate: 40,
            },
          ],
        },
        {
          id: "c-b",
          symbol: "b",
          historical_count: 90,
          confidence: 0.58,
          win_rate: 53,
          children: [
            {
              id: "c-b-a",
              symbol: "a",
              historical_count: 50,
              confidence: 0.62,
              win_rate: 56,
            },
            {
              id: "c-b-d",
              symbol: "d",
              historical_count: 30,
              confidence: 0.48,
              win_rate: 46,
            },
          ],
        },
      ],
    },
    {
      id: "d",
      symbol: "d",
      historical_count: 80,
      confidence: 0.25,
      win_rate: 30,
      children: [
        {
          id: "d-a",
          symbol: "a",
          historical_count: 35,
          confidence: 0.2,
          win_rate: 28,
        },
        {
          id: "d-c",
          symbol: "c",
          historical_count: 30,
          confidence: 0.32,
          win_rate: 36,
        },
      ],
    },
  ],
};
