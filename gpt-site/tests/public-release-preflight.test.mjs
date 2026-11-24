import test from 'node:test';
import assert from 'node:assert/strict';
import {assessPublicRelease,EVIDENCE_CHECKS} from '../scripts/public-release-preflight.mjs';

const configured={
 G2B_PUBLIC_AUTH_ORIGIN:'https://g2b.example.kr',
 G2B_PUBLIC_SESSION_PEPPER:'P'.repeat(64),
 G2B_PUBLIC_SERVICE_SYNC_TOKEN:'S'.repeat(64),
 KAKAO_REST_API_KEY:'kakao-id',KAKAO_CLIENT_SECRET:'kakao-secret',
 NAVER_CLIENT_ID:'naver-id',NAVER_CLIENT_SECRET:'naver-secret',
 GOOGLE_CLIENT_ID:'google-id',GOOGLE_CLIENT_SECRET:'google-secret',
};
const attested=Object.fromEntries(EVIDENCE_CHECKS.map(key=>[key,true]));

test('default preflight blocks public launch without verified deployment and secrets',()=>{
 const report=assessPublicRelease();
 assert.equal(report.ready,false);
 assert.equal(report.requires_manual_site_publication,true);
 assert.ok(report.blockers.includes('kakao_real_oauth_passed'));
 assert.ok(report.blockers.includes('existing_d1_migration_0023_verified'));
 assert.ok(report.blockers.includes('origin_https'));
});

test('configured environment is insufficient without individual real-world sign-offs',()=>{
 const report=assessPublicRelease(configured);
 assert.equal(report.ready,false);
 assert.ok(report.blockers.includes('public_visibility_supported_and_approved'));
 assert.equal(report.checks.find(x=>x.id==='session_pepper')?.verified,true);
});

test('manual attestations do not waive broken HTTPS or missing secrets',()=>{
 const report=assessPublicRelease({...configured,G2B_PUBLIC_AUTH_ORIGIN:'http://g2b.example.kr',GOOGLE_CLIENT_SECRET:''},attested);
 assert.equal(report.ready,false);
 assert.deepEqual(report.blockers.sort(),['google_application','origin_https']);
});

test('only complete configuration and explicit evidence yields ready',()=>{
 const report=assessPublicRelease(configured,attested);
 assert.equal(report.ready,true);
 assert.equal(report.blockers.length,0);
 assert.equal(report.requires_manual_site_publication,true);
});

test('report never includes secret values, original identity or credentials',()=>{
 const report=assessPublicRelease(configured,attested);
 const result=JSON.stringify(report);
 for(const value of [configured.G2B_PUBLIC_SESSION_PEPPER,configured.G2B_PUBLIC_SERVICE_SYNC_TOKEN,'kakao-secret','naver-secret','google-secret','g2b.example.kr'])assert.equal(result.includes(value),false);
});

test('nonboolean evidence cannot accidentally satisfy release conditions',()=>{
 const report=assessPublicRelease(configured,Object.fromEntries(EVIDENCE_CHECKS.map(x=>[x,'true'])));
 assert.equal(report.ready,false);
 assert.equal(report.blockers.filter(x=>EVIDENCE_CHECKS.includes(x)).length,EVIDENCE_CHECKS.length);
});
