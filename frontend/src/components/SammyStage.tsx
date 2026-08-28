import { useEffect, useRef } from 'react';
import {
	useRive,
	useViewModel,
	useViewModelInstance,
	useViewModelInstanceBoolean,
	useViewModelInstanceTrigger,
} from '@rive-app/react-canvas';
import './SammyStage.css';

const ARTBOARD = 'Artboard 2';
const STATE_MACHINE = 'State Machine 1';
const VIEW_MODEL = 'ViewModel1';

type SammyStageProps = {
	isTalking: boolean;
	isShocked?: boolean;
	isThinking?: boolean;
};

export function SammyStage({ isTalking, isShocked = false, isThinking = false }: SammyStageProps) {
	const readyFired = useRef(false);

	const { rive, RiveComponent } = useRive({
		src: '/sammy.riv',
		artboard: ARTBOARD,
		stateMachines: STATE_MACHINE,
		autoplay: true,
		autoBind: true,
	});

	const viewModel = useViewModel(rive, { name: VIEW_MODEL });
	const viewModelInstance = useViewModelInstance(viewModel, {
		useDefault: true,
		rive,
	});

	const { trigger: fireIsReady } = useViewModelInstanceTrigger('IsReady', viewModelInstance);
	const { setValue: setTalking } = useViewModelInstanceBoolean(
		'isTalkingHappy2',
		viewModelInstance,
	);
	const { setValue: setShocked } = useViewModelInstanceBoolean(
		'isShocked2',
		viewModelInstance,
	);
	const { setValue: setThinking } = useViewModelInstanceBoolean(
		'isThinking',
		viewModelInstance,
	);

	useEffect(() => {
		if (!rive || !viewModelInstance || readyFired.current) return;
		try {
			fireIsReady();
			readyFired.current = true;
		} catch {
			/* Property may be named differently in the .riv file */
		}
	}, [rive, viewModelInstance, fireIsReady]);

	useEffect(() => {
		try {
			setTalking(isTalking);
		} catch {
			/* Property may be named differently in the .riv file */
		}
	}, [isTalking, setTalking]);

	useEffect(() => {
		try {
			setShocked(isShocked);
		} catch {
			/* Property may be named differently in the .riv file */
		}
	}, [isShocked, setShocked]);

	useEffect(() => {
		try {
			setThinking(isThinking);
		} catch {
			/* Property may be named differently in the .riv file */
		}
	}, [isThinking, setThinking]);

	return (
		<div className="sammy-stage" aria-hidden="true">
			<RiveComponent className="sammy-stage__canvas" />
		</div>
	);
}
