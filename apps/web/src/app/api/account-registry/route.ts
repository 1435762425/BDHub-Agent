import {createRegistryHandlers} from '../../../server/account-registry/bridge.ts';
export const runtime='nodejs';
export const dynamic='force-dynamic';
export const {GET,POST}=createRegistryHandlers();
