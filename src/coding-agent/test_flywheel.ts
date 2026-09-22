import { DBOS } from '@dbos-inc/dbos-sdk';
import { CodingAgentWorker } from './src/worker.js';

async function main() {
    await DBOS.launch();
    console.log("DBOS launched. Running Data Flywheel Pipeline manually...");
    const handle = await DBOS.startWorkflow(CodingAgentWorker).dataFlywheelPipeline(new Date(), new Date());
    await handle.getResult();
    console.log("Data Flywheel Pipeline finished.");
    process.exit(0);
}

main().catch(console.error);
