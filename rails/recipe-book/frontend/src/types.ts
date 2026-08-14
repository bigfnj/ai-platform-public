export type Kind = "meal" | "beverage";

export interface Stats { total: number; meals: number; beverages: number; categories: number; spirits: number }
export interface Category { name: string; count: number; kind: string }
export interface Spirit { name: string; count: number }
export interface TagRef { id: number; name: string; color: string }
export interface Rating { stars: number; note: string }

// admin recipe-icon (re)generation status (GET /api/icons/status)
export interface IconStatus {
  ready: number; pending: number; total: number;
  running: boolean; phase: string | null;
  started_at: string | null; finished_at: string | null;
  last: Record<string, unknown> | null;
}

export interface RecipeSummary {
  id: string;
  title: string;
  category: string;
  kind: Kind;
  ingredient_count: number;
  step_count: number;
  is_collection: boolean;
  base_spirits: string[];
  primary_spirit: string;
  glass: string;
  technique: string;
  icon_status: string;
  favorite?: boolean;
  rating?: Rating | null;
  tags?: TagRef[];
}

export interface Section { heading: string; ordered: boolean; items: string[] }

export interface RecipeDetail extends RecipeSummary {
  meta: string;
  source: string;
  ingredients: string[];
  instructions: string[];
  shopping_list: string[];
  extra_sections: Section[];
  rel_path: string;
  attributes: string[];        // effective dietary/allergen tags (after manual overrides)
  attributes_auto: string[];   // the auto-classifier output (for reset / diff)
}

export interface RecipeDraft {
  title: string; meta: string; kind: Kind; category: string;
  ingredients: string[]; instructions: string[]; shopping_list: string[]; source: string;
}
export interface DraftSuggest { kind: string; category: string; spirit: string; source: string }
export interface DraftResult { draft: RecipeDraft; questions: string[]; suggest?: DraftSuggest }
export interface DupMatch { id: string; title: string; category: string; score: number }

export interface RecipeList { items: RecipeSummary[]; total: number; limit: number; offset: number }
export interface PlannerEntry {
  id: number; date: string; slot: string; recipe_id: string | null;
  title: string; servings: number; recipe: RecipeSummary | null;
}
export interface PlanProposalItem {
  date: string; slot: string; ptype: "meal" | "cocktail" | "wine";
  recipe_id: string | null; title: string; summary: RecipeSummary | null; why: string;
}
export interface PlanSettings {
  plan_retention_days: number;
  plan_recency_days: number;
  is_admin: boolean;
  defaults: { plan_retention_days: number; plan_recency_days: number };
  ranges: { plan_retention_days: [number, number]; plan_recency_days: [number, number] };
}
export interface ShoppingItem { key: string; label: string; detail: string; sources: string[]; checked: boolean }
export interface InvItem { id: number; name: string; kind: string }
export interface MatchResult extends RecipeSummary {
  matched_ingredients: string[];
  missing_ingredients: string[];
  coverage: number;
  makeable: boolean;   // every required ingredient on hand
  need?: string;       // the single missing bottle/ingredient when makeable === false
}
