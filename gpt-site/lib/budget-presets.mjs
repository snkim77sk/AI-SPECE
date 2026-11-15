/** Original active budget quick-search options. They filter stored rows only;
 * they are not collection categories, forecasts, or provider request arguments. */
export const BUDGET_SEARCH_PRESETS=[
 {label:'조명',options:[['조명','조명'],['LED','LED 조명'],['가로등','가로등'],['보안등','보안등'],['실내조명','실내조명'],['평판등','평판등'],['다운라이트','다운라이트'],['투광등','투광등'],['터널등','터널등'],['등주','등주'],['경관조명','경관조명']]},
 {label:'연관 사업',options:[['신축','신축사업'],['건립','건립사업'],['증축','증축사업'],['리모델링','리모델링'],['도로개설','도로개설'],['도로정비','도로정비'],['도로개선','도로개선'],['공원','공원'],['주차장','주차장'],['터널','터널'],['교량','교량'],['도시재생','도시재생'],['경관개선','경관개선'],['보행환경','보행환경']]},
];
const allowed=new Set(BUDGET_SEARCH_PRESETS.flatMap(group=>group.options.map(([value])=>value)));
export function budgetSearchInput(params){
 for(const key of ['q','preset_q'])if(params.getAll(key).length>1)throw Error('DUPLICATE_BUDGET_SEARCH_FILTER');
 const preset=(params.get('preset_q')||'').trim(),q=(params.get('q')||'').trim();
 if(preset&&!allowed.has(preset))throw Error('INVALID_BUDGET_SEARCH_PRESET');
 const effective=[preset,q].filter(Boolean).join(' ');if(effective.length>100)throw Error('BUDGET_SEARCH_TOO_LONG');
 const terms=[...new Set(effective.split(/\s+/).filter(Boolean))];if(terms.length>8)throw Error('BUDGET_SEARCH_TOO_MANY_WORDS');
 return{preset,q,effective,terms};
}
