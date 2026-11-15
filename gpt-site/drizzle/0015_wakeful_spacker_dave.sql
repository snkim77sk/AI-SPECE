CREATE TABLE `institution_pattern_aliases` (
	`run_id` text NOT NULL,
	`region_key` text NOT NULL,
	`category` text NOT NULL,
	`alias` text NOT NULL,
	`org_key` text NOT NULL,
	`fiscal_year` integer NOT NULL,
	PRIMARY KEY(`run_id`, `region_key`, `category`, `alias`, `org_key`, `fiscal_year`),
	FOREIGN KEY (`run_id`,`region_key`,`org_key`,`category`,`fiscal_year`) REFERENCES `institution_pattern_years`(`run_id`,`region_key`,`org_key`,`category`,`fiscal_year`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
ALTER TABLE `institution_pattern_runs` ADD `alias_version` text DEFAULT '' NOT NULL;