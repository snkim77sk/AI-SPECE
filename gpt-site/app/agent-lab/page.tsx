import {notFound} from 'next/navigation';
import AgentDemo from '../../components/agent-demo';
export default function AgentLab(){
 if(process.env.NODE_ENV !== 'development')notFound();
 return <AgentDemo/>;
}
