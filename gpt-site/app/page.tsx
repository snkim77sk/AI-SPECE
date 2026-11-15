'use client';
import {useEffect,useState} from 'react';
import Dashboard from './dashboard/page';
import DeliveryWorkspace from '../components/delivery-workspace';
export default function Home(){const[legacy,setLegacy]=useState<string|null>(null);useEffect(()=>{const restore=()=>{const tab=new URLSearchParams(window.location.search).get('tab');setLegacy(['data','smart','vendors','agencies','collect','scope'].includes(tab||'')?tab:null);};restore();window.addEventListener('popstate',restore);return()=>window.removeEventListener('popstate',restore);},[]);return legacy?<DeliveryWorkspace key={legacy} initialTab={legacy}/>:<Dashboard/>;}
