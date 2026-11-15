import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
test('advertised G2B batches stay at 8 with a 90 second ceiling',()=>{
 const workflow=JSON.parse(readFileSync(new URL('../public/collection-workflow.json',import.meta.url),'utf8'));
 assert.equal(workflow.proposedSchedule.maximumG2BPagesPerRun,8);
 const route=readFileSync(new URL('../app/api/automation/route.ts',import.meta.url),'utf8');
 assert.match(route,/maximumPagesPerRun:\s*\{g2b:8,/);
 assert.match(JSON.stringify(workflow),/90/);
 assert.match(route,/onePagePerCall:true/);
});
