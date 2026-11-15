CREATE TABLE `budget_replay_state` (
	`session_id` text PRIMARY KEY NOT NULL,
	`root_id` text NOT NULL,
	`parent_id` text,
	`replay_count` integer NOT NULL,
	`failure_page` integer,
	`error` text,
	`successor_id` text,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_budget_replay_parent` ON `budget_replay_state` (`parent_id`);